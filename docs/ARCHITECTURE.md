# Architecture

LoL Coach is a **neurosymbolic** drafting coach. The word matters: it is two
systems joined at a narrow, auditable seam. A deterministic engine produces a
decision and a trace of *why*; a language model turns that trace into prose and
is structurally forbidden from adding to it.

> **Invariant:** the LLM never decides picks. If a capability can't be expressed
> as an engine computation with source-tagged traces, it doesn't ship.

## Layers

```
                     ┌─────────────────────────────────────────────┐
  HTTP  :8000  ───►  │ server/  FastAPI routes                      │
                     └───────────────┬─────────────────────────────┘
                                     │ runs the workflow
                     ┌───────────────▼─────────────────────────────┐
                     │ workflow/  Step-Functions-style executor     │
                     │   ValidateDraft → … → SelectSynergyPick → …  │
                     │   every transition persisted to executions.db│
                     └───────┬───────────────────────┬─────────────┘
                             │ calls (pure)          │ traces in
                     ┌───────▼───────────┐   ┌───────▼─────────────┐
                     │ engine/  decisions │   │ agent/  adapter +   │
                     │  counter + synergy │   │  tools + narrator   │
                     │  TraceLine per pt  │   │  LangChain (Claude/ │
                     └───────┬───────────┘   │  OpenAI) + grounded │
                             │ reads Ontology (champions + relations)
          ┌──────────────────┴──────────────────┐
          │                                     │
 ┌────────▼─────────┐                 ┌─────────▼──────────┐
 │ knowledge/       │                 │ dataprovider/      │
 │ YAML KG + schema │                 │ OP.GG MCP client   │
 │ (champions,      │                 │ + SQLite snapshot  │
 │  relations)      │                 │ (data/snapshot.db) │
 └──────────────────┘                 └────────────────────┘
```

**Boundaries are enforced by convention and an import test**
(`tests/test_engine.py::test_engine_does_not_import_other_layers`):

- `engine/**` is pure: no I/O, no network, no clock, no imports from
  `dataprovider`, `workflow`, `server`, or `agent`. It receives an `Ontology`
  (champions, positions, relations) by dependency injection, so it is trivially
  unit-testable. The ontology already carries everything the engine needs — the
  snapshot is only a build-time input to the graph.
- `workflow/**` is the only layer that persists execution history. It does **not**
  touch the chat database: the server loads session memory and writes turns back.
- `dataprovider/**` is the only layer that touches the network and the only layer
  that writes `data/snapshot.db`. It also materializes the reference data (item
  catalog, champion tips/abilities/lore) into `knowledge/seed/reference*.yaml`
  during `build-kg`; `build_kg.py` is the only writer of the seed directory.
- `knowledge/reference.py` loads that materialized reference data at runtime, the
  same way `ontology.py` loads the graph: from checked-in YAML, never the snapshot.
  It is deliberately **lenient** — a missing or malformed file yields an empty
  `ReferenceData` with a WARNING, because reference data is advisory and must not
  gate startup (unlike the ontology, which hard-fails).
- `chat/**` is the only layer that persists chat sessions and their memory
  (`data/chat.db`): the store, the schema, and the memory-tool logic. It depends
  on nothing above it; the server wires it to HTTP.
- `agent/**` is the only layer allowed to call a language model. It has three
  jobs, none of them deciding: **adapt** fuzzy input to a strict board
  (`tools.set_board`), **orchestrate** the deterministic tools (`agent.choose_action`
  → `workflow/workflows/agent_chat.py`), and **narrate** tool results
  (`narrator.narrate` / `narrate_final`). It reads the engine and knowledge layer
  through `agent/tools.py`; it may not assert any number absent from a tool result.
- `observability/**` is optional, cross-cutting instrumentation for the model seam
  (`agent/llm.py`), not a source of behaviour: it records what the agent *did* but
  never changes what it decides. It imports only `config`, so any layer may wrap a
  run in `tracing.trace(...)` without a cycle. The Langfuse SDK is imported lazily
  and only when configured, so the package is not a hard dependency.

## Build & run

```bash
# backend (editable install with dev tools)
pip install -e ".[dev]"

# data backbone (run once — throttled and resumable)
python -m lolcoach.cli fetch --limit 40     # OP.GG -> data/snapshot.db
python -m lolcoach.cli build-kg             # snapshot -> knowledge/seed/*.yaml
python -m lolcoach.cli kg-report            # seed YAML -> data/kg-report.html (open in a browser)

# API
uvicorn lolcoach.main:app --reload          # http://127.0.0.1:8000

# frontend
cd frontend && pnpm install && pnpm dev     # http://localhost:5173

# verification (what init.sh runs)
ruff check . && mypy src && pytest tests -q

# containers (optional — CI builds the same two images)
docker build -t lol-coach-backend .          # API image: uvicorn on :8000
docker build -t lol-coach-frontend frontend  # SPA image: nginx on :80, proxies /api
docker compose up --build                    # both: API :8000, web :8080
```

Configuration comes from the repo-root `.env` (copy `.env.example`). Each
entrypoint (`main.py`, `cli.py`) calls `config.load_env()` once before
`get_settings()`; it loads `.env` with `override=True`, so the credentials you
paste there win over a stale value in the shell. `get_settings()` itself only
reads `os.environ`, which keeps it controllable under `monkeypatch` in tests.

### CI/CD

`.github/workflows/ci.yml` runs on every push to `main`, every pull request, and
on manual dispatch (with `cancel-in-progress`). Three jobs:

- **backend** — matrix Python 3.11 + 3.12: `pip install -e ".[dev]"` →
  `ruff check .` → `mypy src` → `pytest tests -q`. The suite is hermetic (no
  model, no network), so CI needs no secrets.
- **frontend** — pnpm 12.9.1 + Node 22: `pnpm install --frozen-lockfile` →
  `pnpm typecheck` → `pnpm build`.
- **docker** — needs both, builds the two images with buildx and a layer cache
  (`push: false`; CI builds, it does not publish).

The two Dockerfiles are the deployment unit. `Dockerfile` builds the API on
`python:3.12-slim`, installs the package, drops to a non-root user, mounts
`/data`, and healthchecks `/api/health` via the stdlib. `frontend/Dockerfile`
builds the SPA on `node:22-alpine`, then serves `dist/` from `nginx:1.27-alpine`;
`frontend/nginx.conf` adds the SPA fallback, immutable-asset caching, and an
`/api/` proxy to the `backend` service. `docker-compose.yml` runs the pair on a
named data volume.

## Processes

| Process | Command | Port | Talks to |
|---------|---------|------|----------|
| API | `uvicorn lolcoach.main:app` | 8000 | snapshot.db (read), executions.db (read/write), chat.db (read/write), OP.GG MCP (fetch + the two live lookups), LLM provider (orchestrate + narrate only), Langfuse (optional, traces) |
| Web | `pnpm dev` (Vite) | 5173 | API via `VITE_API_BASE_URL`, the Data Dragon CDN (portraits) |
| Fetch job | `python -m lolcoach.cli fetch` | — | OP.GG MCP → snapshot.db |
| KG build | `python -m lolcoach.cli build-kg` | — | snapshot.db → `knowledge/seed/*.yaml` (graph + reference) |
| KG report | `python -m lolcoach.cli kg-report` | — | `knowledge/seed/*.yaml` → `data/kg-report.html` (static, offline) |
| Langfuse (optional) | `bash scripts/langfuse.sh up` | 3000 | receives the agent's traces; stores them in its own stack |
| Backend image | `docker compose up backend` | 8000 | same as the API, with `/data` on a named volume |
| Frontend image | `docker compose up frontend` | 8080 | the API, via nginx `/api/` → `backend:8000` |

`main.py` is the single entrypoint that calls `configure_logging()` exactly once,
then constructs the FastAPI app. Domain modules only create module-level loggers.

## Data directory

Everything runtime lives under `data/` (gitignored). Four SQLite databases with
different lifecycles, deliberately kept apart:

- `data/snapshot.db` — the OP.GG statistical snapshot. **Replaceable and
  rebuildable** from the MCP server; written only by `dataprovider/`.
  Tables: `champions`, `champion_stats`, `champion_roles`, `matchups`,
  `synergies`, `analysis_done`, `champion_reference`, `champion_builds`,
  `champion_runes`, `items`, `meta`.
- `data/executions.db` — the immutable execution history (the traceability
  feature). **Precious**; written through by the workflow executor on every state
  transition. Tables: `executions`, `execution_events`.
- `data/chat.db` — chat sessions and the agent's session memory. **Mutable**
  product state: create, rename, and delete freely. Tables: `sessions`,
  `session_messages`, `session_notes`. Written only by `chat/store.py`.
- `data/eval.db` — agent-evaluation run history, so a failing golden scenario opens
  in the Trace Inspector. **Disposable**; written only by the eval runner.

Splitting them means "refresh the meta data", "keep the audit trail", and "delete
a conversation" never have to negotiate.

The snapshot is populated by `dataprovider/fetch.py` from the OP.GG MCP server.
OP.GG returns a **typed repr**, not JSON (`class Name: a,b,c` headers declaring
field order, then a nested positional expression); `dataprovider/typed_repr.py`
parses it. The fetch is throttled and resumable (`analysis_done` marks completed
`(champion, position)` pairs), and the alternate-mode roster variants (`Jade_*`,
id ≥ 60000) are excluded. `dataprovider/build_kg.py` turns the snapshot into the
YAML graph: counter direction is derived from the measured win rate (not the
tool's label), weights are win-rate delta × sample-size confidence, and each edge
carries an auto-generated statistical `reason`.

## Materialized reference data

Not everything the coach can talk about is a *decision*. Items, runes, skill
order, champion tips, and abilities are **reference** facts: stable, small, and
useful next to a recommendation, but they never decide anything. They are
materialized the same way the graph is — fetch once, snapshot, emit YAML, read
YAML at runtime — rather than fetched live per question, so a chat run never
depends on the network being up.

```
lol_list_champion_details ─┐
lol_list_items           ─┼─► fetch pass ─► snapshot tables ─┐
analysis core/boots/runes ─┘                                 │
                                                             ▼
                        build-kg ─► knowledge/seed/{reference,builds,items}.yaml
                                                             │
                                                             ▼
                       knowledge/reference.py ─► ToolBox._reference
                                                ─► get_champion_tips / get_champion_build
```

- **Fetch.** `fetch.py::_load_reference` runs after the analyses. Champion details
  arrive in batches of 10 (`lol_list_champion_details`), so `data.champions[]` is
  matched by `id` (falling back to display name); `lol_list_items` is a single call
  returning plain **JSON** (not the typed repr — `opgg_mcp.py::_parse_result_text`
  accepts both). Batches are addressed by the *display name* squashed to
  UPPER_SNAKE (`text.upper_snake`): OP.GG wants `CHOGATH` for `Cho'Gath` and
  `DR_MUNDO` for `Dr. Mundo`, which is **not** the Riot key uppercased. A failed
  batch is simply not marked done, so a re-run retries it. Build rows come from the
  per-`(champion, lane)` analysis call we already make, so the extra cost is ~18
  calls, not a second full pull. A row is written even when tips are empty, and
  `reference_done` marks the champion, so an interrupted pull resumes. Summoner
  spells and stat shards are deliberately **not** requested: OP.GG returns bare ids
  for both (`ids_names: [4, 12]`), which would materialize as meaningless numbers.
- **Build.** `build_kg.py` emits `reference.yaml`, `builds.yaml`, and `items.yaml`
  (with a `meta` block each), **always** — an empty list rather than a missing file,
  so the loader has a stable path. The returned `counts` dict is unchanged; the
  reference totals are logged, not returned.
- **Runtime.** `knowledge/reference.py::get_reference()` is `lru_cache`d and loads
  the three files into `ReferenceData(champions, builds, items)`. Missing files are
  a WARNING and an empty result, never an exception.
- **Tools.** `get_champion_tips` and `get_champion_build` are advertised only when
  the data is actually present (`ToolBox.reference_tools`), so advertised always
  equals available. Both are **evidence-only**: their payloads never contain a pick.

### Reference prose and the grounding guard

Tips and ability descriptions are human-written prose that names *other* champions
("great against Yasuo"). If those payloads were walked for champion slugs the way
`suggest_pick`'s are, a single tip would whitelist half the roster and the guard
would stop meaning anything. So `ToolResult.sources` carries the champion slugs a
result is **entitled** to source, and `narrator.py` treats an explicit-source tool
(`get_champion_tips`/`get_champion_build`, plus the live tools) differently: only
`step.sources` is added to the whitelist, and the payload is **not** walked. Every
other tool keeps the existing `champions_in_payloads` walk, so their behavior is
byte-for-byte unchanged.

`sources` lives on `ToolStep`, not in `payload`, so it never reaches the model —
`_llm_action` serializes `step.payload` alone, and a slug list in the JSON would be
an invitation to echo it.

The cost of a strict name guard is that a narrated reply quoting a tip which names
another champion is discarded; the `_FINAL_SYSTEM_PROMPT` says not to do that, and
`offline_final_text` has tips/build branches so the grounded fallback is still
useful when a reply is dropped.

## Live lookups

Two things cannot be checked in, because they change with the player and the
patch: a summoner's rank, and the guide for one specific lane matchup. Those are
fetched **during** a chat run, by the agent, only when a question asks for them.

```
agent/tools.py::LiveDataProvider   (Protocol - no network import)
            ▲
            │ injected by the composition root
            │
server/app.py ──► dataprovider/live.py::OpggLiveProvider ──► OP.GG MCP
```

- **The seam.** `agent/**` may not import `dataprovider`, so the interface is a
  `Protocol` declared in `agent/tools.py` and the implementation lives in
  `dataprovider/live.py`. `server/app.py` is the composition root and injects it
  (`build_live_provider()`, skipped entirely when `LOL_COACH_ENABLE_LIVE_TOOLS` is
  off). A tool with no provider is not advertised *and* returns a failed result,
  so advertised always equals available.
- **Singleton.** The provider is `lru_cache`d. Its TTL cache and min-interval gate
  live on the instance, so a per-request provider would cache and throttle nothing.
  It takes no arguments because `Settings` carries a dict and is unhashable.
- **Defensive by design.** A short timeout and a one-retry budget (a chat turn is
  waiting), a monotonic min-interval gate under a `threading.Lock` (FastAPI runs
  these sync endpoints in a threadpool; OP.GG is a public server), and a TTL cache
  keyed by the exact call. A failure raises `LiveDataError`, which the tool loop
  turns into an `ok=False` step and carries on — a dead upstream never fails a run.
- **Payloads are shaped, not passed through.** The raw profile and guide responses
  are tens of kilobytes; `live.py` reduces them to the fields the coach reports.
  `get_summoner_profile` returns rank/LP/record and **no champion names at all** —
  the guard would otherwise have to license naming the player's champion pool,
  which is a player fact, not a draft fact. `get_lane_matchup_guide` returns the
  written tip, the lane-advantage verdicts, and that champion's win rate in the
  lane (taken from the lane's own row, not the all-lane average), and declares
  `sources=(champion, opponent)` — the pair the user named.

## Draft-eval pipeline

The pipeline is expressed as workflow states, not as a function. Task bodies are
registry keys (`workflow/workflows/draft_eval.py`), so the definition stays
inspectable and each step is individually traceable. `workflow/types.py` holds the
state types (`TaskState`/`ChoiceState`/`SucceedState`/`FailState`) and validates
the graph at construction; `workflow/executor.py` interprets it and writes every
event through `workflow/store.py` to `data/executions.db` as it happens. A task
that raises emits `STATE_FAILED` (with traceback) then `EXECUTION_FAILED`.

```
ValidateDraft ──► IsValid (Choice) ──invalid──► Fail
     │ valid
     ▼
LoadKnowledge ──► BuildCandidates ──► ScoreCounters ──► ScoreSynergies
     ▼
SelectCounterPick ──► SelectSynergyPick ──► AssembleTrace
     ▼
PicksExist (Choice) ──none──► Fail
     │ picks
     ▼
Succeeded
```

- **Engine step detail** (`engine/scoring.py`, pure): `draft_state.py` describes
  the board (allies/enemies × picks/bans, open roles); `scoring.py` scores every
  available champion by **counter score** (Σ over enemy picks of the counter edge
  weight, which is win-rate delta × sample-size confidence) and **synergy score**
  (Σ over allies of the synergy edge weight). Each contributing relation emits a
  `TraceLine(source, text, delta)`. Exactly one counter-pick (best counter fitting
  an open role) and one synergy-pick are returned, de-duplicated so the pair is
  actionable, with deterministic tie-breaks. `explain.py` formats them.
- **Traces** (`engine/trace.py`) are the interface between the engine and
  everything downstream. A trace line names its `source` — a knowledge-graph
  relation id, a snapshot stat, or an engine rule — and the delta it contributed.

## Agent chat loop

The chat front door (`POST /api/agent/chat`) is a **second workflow**
(`workflow/workflows/agent_chat.py`), so the agent's tool-loop is itself a
traceable state machine — the reason a user can open the Trace Inspector and see
exactly which tools the agent chose and in what order.

```
ParseInput ─► Think (Task) ─► Route (Choice)
                 ▲                │ tool
                 └──── RunTool ◄───┤
                                  │ final
                                  ▼
                              Finalize ─► Succeeded
                                  │ over max_tool_calls (8)
                                  ▼
                              MaxSteps (Fail)
```

- `agent/tools.py` — the deterministic tool surface. `set_board` resolves loose
  names to slugs via `knowledge/resolve.py::NameResolver`; `suggest_pick` wraps
  the pure `engine/scoring.py::evaluate_draft` and is **the only source of a
  pick**; `explain_matchup` and `get_champion` return evidence;
  `get_champion_tips` and `get_champion_build` return materialized reference data
  (see *Materialized reference data*). There is no tool that lets the model
  introduce a champion of its own.
- `agent/agent.py::choose_action` — runs the tool-loop over `langchain` (system
  prompt: adapt/orchestrate/narrate only, picks come only from `suggest_pick`) and
  emits a JSON-safe action dict. There is **no offline planner**: a missing client,
  or any failing provider call, raises `LlmUnavailableError`, which fails the
  `Think` state and the whole execution. The agent never fabricates an action.
- `agent/llm.py` — a provider-neutral client (`LlmMessage`/`LlmReply`, vision
  image parts) selected by `LOL_COACH_LLM_PROVIDER`; `build_client` returns `None`
  when the key or package is missing. The endpoint is always passed explicitly
  (`LOL_COACH_LLM_BASE_URL`, else the provider's real API), so an ambient
  `ANTHROPIC_BASE_URL` cannot redirect it. `LOL_COACH_LLM_EXTRA_HEADERS` (a JSON
  object) adds request headers, for gateways/proxies that require them.
- Every completed tool call is a `ToolStep` carried in workflow `data`, so the
  loop's `Think`/`RunTool` events render the agent's call journey in the Inspector.

## Chat sessions & agent memory

The chat is **stateful per session** (ChatGPT/Gemini style). A *session*
(`chat/store.py`, `data/chat.db`) holds an ordered transcript, the board it is
working from, the memory notes the agent recorded, and a hard cap of
`LOL_COACH_MAX_QUESTIONS` (default **5**) user questions.

```
POST /api/sessions            create a session          201
GET  /api/sessions            list (most-recent first)
GET  /api/sessions/{id}       transcript + board + notes + counters
PATCH/DELETE /api/sessions/{id}
POST /api/agent/chat          {message, image?, session_id?} → run + persist
```

`POST /api/agent/chat` with a `session_id` is the stateful path. The server:

1. rejects the call with **409** when the session already holds its question cap
   (and **404** for an unknown session; **503** before consuming a slot when no
   LLM is configured);
2. resolves the board (the request's, else the session's) and loads the session's
   prior turns and notes;
3. appends the user question — the slot is consumed here, so the cap bounds
   *requests*, not answers;
4. runs the agent workflow with `turns`, `notes`, and `memory=true` in the run's
   initial data (the workflow itself never touches the DB);
5. writes the assistant reply and any notes the agent recorded back to the
   session, and returns the updated `question_count` / `remaining_questions`.

The **transcript is the agent's memory**. Prior turns are replayed to the model
as conversation context (the same `turns` channel a session-less call uses), and
the memory notes are re-injected as a system message on every turn. Two memory
tools (`chat/memory.py`, offered only when `memory=true`) let the agent manage
that memory itself:

| Tool | What it does |
|------|--------------|
| `recall_history` | returns this session's earlier turns and kept notes, for when the model wants to re-read rather than rely on context |
| `remember` | keeps a short note (a board detail, a stated constraint) that is re-read on every later turn |

Memory-tool results travel in the workflow `data` like any other tool payload, so
they render in the Trace Inspector; the server — not the workflow — owns
persistence, keeping `workflow/` free of the chat database.

Sessions carry no authentication: with auth out of scope, the API trusts the
caller and stores nothing personal beyond the conversation itself.

## Narrator seam

`agent/narrator.py` has two entry points, both rephrase-only:

- `narrate(recommendation)` — explains one engine recommendation record (the
  draft-eval path).
- `narrate_final(question, tool_results, board)` — turns a whole agent tool-loop
  into coach prose.

Both use the provider-neutral `agent/llm.py` client. `narrate_final` is grounded
**structurally**, not just by prompt: `unsourced_champions(text, allowed, ontology)`
scans the model's reply for any known champion not present in the tool results; on
a violation the reply is discarded and the deterministic offline summary is
returned instead. `offline_final_text` builds that summary from the tool payloads
alone.

The two entry points differ on a missing model. `narrate_final` (agent chat)
**requires** a client: with none configured, none available, a failing call, or a
blank reply it raises `LlmUnavailableError`, the `Finalize` state fails, and the
run returns no answer. `POST /api/agent/chat` additionally checks up front and
returns **503** — without creating an execution — when no LLM is configured at
all. The draft-eval `narrate` path is unchanged and still falls back to
`offline_text`. In every case the engine, traces, and Trace Inspector keep
working.

## AI observability (Langfuse)

The in-house Trace Inspector records what the *workflow* did — states, inputs,
timings, logs. It deliberately does not record the model's traffic: the prompt
sent, the completion returned, and the tokens and cost they consumed. Those live
in **Langfuse**, attached at the one seam where a model is ever consulted.

```
server/app.py (agent-chat route)  ─┐   observability/tracing.py::trace(...)
eval/runner.py (per scenario)      ─┼─►   sets trace attrs + publishes callbacks
server/app.py (narrate, rerun)     ─┘         │
                                              ▼  ContextVar
                              agent/llm.py::_LangChainClient.complete
                                              │  config={"callbacks":[...]}
                                              ▼
                              LangChain run ─► Langfuse (prompt/completion/tokens/cost)
```

- **The seam.** Every model call in the product — the agent's `Think` decisions and
  its final narration — goes through `agent/llm.py::LlmClient.complete`. Tracing is
  attached there and nowhere else, so what is captured is exactly what ships.
- **One trace per run.** `tracing.trace(settings, name=..., session_id=..., ...)` wraps
  an `executor.run(...)` call. It opens a root span, sets trace-level attributes
  (the agent run's name, the chat `session_id` so a conversation groups in Langfuse's
  Sessions view, tags, and dimensions), and publishes a LangChain callback handler on
  a `ContextVar`. `llm.py` reads that ContextVar and passes the callbacks to
  `runnable.invoke(...)`, so every generation under the span joins one trace. All
  entry points that run the agent are wrapped: the agent-chat route, narration, the
  rerun route, and each eval scenario.
- **Provider-neutral and optional.** The seam carries a callback handler, not a
  provider: the same trace works for Anthropic or an OpenAI-compatible endpoint.
  Langfuse is enabled only when both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`
  are set (`tracing.configured`); with neither, `trace()` is a no-op and the SDK is
  never imported. A Langfuse setup failure is logged and skipped — observability can
  never fail a run, and the app runs unchanged without the `langfuse` package.
- **No influence on decisions.** Tracing observes the seam; it does not gate, retry,
  or alter a call. The engine still decides, the narrator still only rephrases, and
  the grounding guard is untouched.

Run a local server with `bash scripts/langfuse.sh up` (see `docs/RELIABILITY.md`).



Every `POST /api/draft/evaluate` (and every `POST /api/agent/chat`) creates an
execution. The workflow executor writes each transition through to
`data/executions.db` as it happens, so a crash mid-run still leaves a faithful
partial history — which is the entire reason the executor is in-house.

The Inspector renders an execution as an **n8n-style node graph**, not a flat list:

- **Topology from the definition, not the run.** `Workflow.to_dict()`
  (`workflow/types.py`) serializes the static graph — every state with its type,
  task key, `next`, choice rules, and default/error targets. `GET
  /api/executions/{id}` attaches it as `graph` (`server/app.py::_topology`), so the
  UI draws the *whole* workflow (including branches the run never took) and paints
  execution state over it. The frontend falls back to inferring nodes from the
  event stream if `graph` is absent.
- **Execution overlay.** Events are grouped into per-state occurrences; each node
  shows status (`succeeded` / `failed` / `running` / `not_run`), duration, visit
  count (`×N` for loops), and a log-count badge. The taken path is highlighted;
  untaken branches stay dimmed. This is what makes the agent's `Think ⇄ RunTool`
  loop legible at a glance.
- **Per-state logs.** The executor attaches a thread-filtered
  `logging.Handler` (`_LogCapture` in `workflow/executor.py`) around each state
  body and persists the drained records on the `STATE_ENTERED`/`STATE_*` event
  (`Event.logs`, column `execution_events.logs`). The Inspector's node detail panel
  shows Input / Output / Logs tabs — the logs tab renders the exact
  `{timestamp, level, logger, message, data?, exception?}` records the state's code
  emitted. This is the "show me what the code in this step actually did" view.
- **Rerun.** `POST /api/executions/{id}/rerun` replays the stored `input` through
  the same workflow definition and returns the new `execution_id` (the Inspector
  selects it). The original execution is never mutated — history stays immutable.

Schema note: `execution_events.logs` is added by `store.py::_migrate` via
`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`, so existing databases upgrade in
place and old rows read back as `[]`.
