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
- `dataprovider/**` is the only layer that touches the network and the only layer
  that writes `data/snapshot.db`.
- `workflow/**` is the only layer that persists execution history.
- `agent/**` is the only layer allowed to call a language model. It has three
  jobs, none of them deciding: **adapt** fuzzy input to a strict board
  (`tools.set_board`), **orchestrate** the deterministic tools (`agent.choose_action`
  → `workflow/workflows/agent_chat.py`), and **narrate** tool results
  (`narrator.narrate` / `narrate_final`). It reads the engine and knowledge layer
  through `agent/tools.py`; it may not assert any number absent from a tool result.

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
```

Configuration comes from the repo-root `.env` (copy `.env.example`). Each
entrypoint (`main.py`, `cli.py`) calls `config.load_env()` once before
`get_settings()`; it loads `.env` with `override=True`, so the credentials you
paste there win over a stale value in the shell. `get_settings()` itself only
reads `os.environ`, which keeps it controllable under `monkeypatch` in tests.

## Processes

| Process | Command | Port | Talks to |
|---------|---------|------|----------|
| API | `uvicorn lolcoach.main:app` | 8000 | snapshot.db (read), executions.db (read/write), OP.GG MCP (fetch only), LLM provider (orchestrate + narrate only) |
| Web | `pnpm dev` (Vite) | 5173 | API via `VITE_API_BASE_URL` |
| Fetch job | `python -m lolcoach.cli fetch` | — | OP.GG MCP → snapshot.db |
| KG build | `python -m lolcoach.cli build-kg` | — | snapshot.db → `knowledge/seed/*.yaml` |
| KG report | `python -m lolcoach.cli kg-report` | — | `knowledge/seed/*.yaml` → `data/kg-report.html` (static, offline) |

`main.py` is the single entrypoint that calls `configure_logging()` exactly once,
then constructs the FastAPI app. Domain modules only create module-level loggers.

## Data directory

Everything runtime lives under `data/` (gitignored). Two SQLite databases with
different lifecycles, deliberately kept apart:

- `data/snapshot.db` — the OP.GG statistical snapshot. **Replaceable and
  rebuildable** from the MCP server; written only by `dataprovider/`.
  Tables: `champions`, `champion_stats`, `matchups`, `synergies`, `meta`.
- `data/executions.db` — the immutable execution history (the traceability
  feature). **Precious**; written through by the workflow executor on every state
  transition. Tables: `executions`, `execution_events`.

Splitting them means "refresh the meta data" and "keep the audit trail" never
have to negotiate.

The snapshot is populated by `dataprovider/fetch.py` from the OP.GG MCP server.
OP.GG returns a **typed repr**, not JSON (`class Name: a,b,c` headers declaring
field order, then a nested positional expression); `dataprovider/typed_repr.py`
parses it. The fetch is throttled and resumable (`analysis_done` marks completed
`(champion, position)` pairs), and the alternate-mode roster variants (`Jade_*`,
id ≥ 60000) are excluded. `dataprovider/build_kg.py` turns the snapshot into the
YAML graph: counter direction is derived from the measured win rate (not the
tool's label), weights are win-rate delta × sample-size confidence, and each edge
carries an auto-generated statistical `reason`.

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
  pick**; `explain_matchup` and `get_champion` return evidence. There is no tool
  that lets the model introduce a champion of its own.
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

## Trace Inspector (node-graph UI)

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
