# Session Handoff

## 2026-10-14 (5) — API, grounded narrator, frontend, and the chat agent

### What was accomplished

- **Milestone 5 (api)** — `server/app.py` + `server/schemas.py`: health,
  champions, `POST /api/draft/evaluate` (runs the draft-eval workflow), `POST
  /api/chat`, `GET /api/executions[/{id}]` (Trace Inspector data). 503 on a broken
  graph, 404 on unknown execution.
- **Milestone 6 (narrator)** — `agent/llm.py` (provider-neutral LangChain client
  for Claude/OpenAI, vision image parts, `build_client` → `None` offline) and
  `agent/narrator.py` (`narrate` + `narrate_final`, offline fallback).
- **Milestone 7 (frontend)** — Vite + TS + Tailwind app: Landing, DraftBoard,
  RecommendationCard, TraceInspector, API client.
- **Chat agent** — the chat-first front door where an agent sits between the user
  and the engine:
  - `agent/tools.py` — deterministic tool surface (`set_board`, `suggest_pick`,
    `explain_matchup`, `get_champion`). `suggest_pick` is the **only** source of a
    pick; `set_board` resolves fuzzy names via `NameResolver`.
  - `agent/agent.py` — `choose_action` (LLM tool-loop or deterministic offline
    planner `parse_mentions`), `ToolStep`/`AgentContext`, `offline_hint`.
  - `workflow/workflows/agent_chat.py` — the loop as our state machine:
    `ParseInput → Think → Route → RunTool → … → Finalize | MaxSteps`, budget
    `LOL_COACH_MAX_TOOL_CALLS` (8). The Trace Inspector therefore shows the agent's
    exact call journey.
  - Grounding guard `narrator.unsourced_champions`: a reply naming a champion that
    appears in no tool result is discarded for the offline summary.
  - `POST /api/agent/chat` (stateless; board carried between turns) and a
    chat-first UI (`ChatPanel` + `BoardSummary`), with Draft and Trace Inspector as
    secondary tabs. Image paste/upload is wired through to vision when a key exists.

### Decisions

- **The LLM never decides picks** — restated and now structural: the agent may
  adapt input, orchestrate tools, and narrate, but the only pick it can utter is
  one `suggest_pick` returned (enforced by the guard on top of the prompt).
- **Chat-first, board secondary**; **both Claude + OpenAI** behind
  `LOL_COACH_LLM_PROVIDER`; **vision in scope** (reverses the earlier non-goal).
- Everything degrades to offline: no key ⇒ deterministic planner + offline
  narration, and the engine/traces keep working.

### Verification (green, 2026-10-14)

- `bash init.sh` equivalent green: `ruff check src tests` clean; `mypy src tests`
  clean (55 files); `pytest` → **80 passed** (31 in api/narrator/agent-tools/
  agent-loop).
- `pnpm build` (tsc + vite) succeeds — 37 modules, 169 kB bundle.
- Live API: `POST /api/agent/chat {"message":"jinx vs their zeri"}` →
  `mode:"offline"` (the shell's proxy key errors, caught → offline, as designed),
  board allies `[jinx]` / enemies `[zeri]`, `tool_calls=[set_board, suggest_pick]`,
  counter **Veigar** (adc), synergy **Twisted Fate** (mid); execution `agent-chat`
  shows a 23-event loop (ParseInput → Think → RunTool ×2 → Finalize → Succeeded).

### What remains

- **Browser check** of `/app` (chat → tool chips → board update → Trace Inspector):
  not done here because no browser page was shared; backend + build verified.
- **cicd** is the last `not-started` feature: GitHub Actions, Dockerfiles, and
  wiring `scripts/benchmark.sh` / `cleanup-scanner.sh`.
- Deferred tools (`item_guidance`, `what_would_a_pro_do`, RAG) — additive later.

## 2026-10-14 (4) — in-house workflow state machine

### What was accomplished

- **Workflow package** (`src/lolcoach/workflow/`):
  - `types.py` — `TaskState` / `ChoiceState` (+`ChoiceRule`) / `SucceedState` /
    `FailState` and the frozen `Workflow`; `validate()` rejects unknown `next`,
    missing terminals, key/name mismatch, and empty choices.
  - `events.py` + `ddl.py` — immutable `Event` and the `executions` /
    `execution_events` schema (`UNIQUE(execution_id, seq)`, cascade delete).
  - `store.py` — `ExecutionStore` (SQLite, WAL/busy-timeout/FK) with
    `create_execution` / `append` / `finish` / `list_executions` /
    `get_execution` / `events`.
  - `executor.py` — `WorkflowExecutor`: named task/predicate registries, write
    through every event, `STATE_FAILED` + traceback + `EXECUTION_FAILED` on a
    raising task, max-step guard, injectable clock/monotonic for tests.
  - `workflows/draft_eval.py` — the pipeline as states, with task bodies that
    close over the ontology so only JSON-safe values travel between states:
    ValidateDraft → IsValid → LoadKnowledge → BuildCandidates → ScoreCounters →
    ScoreSynergies → AssembleTrace → PicksExist → Succeeded / DraftInvalid / NoPicks.
- Small engine refactor: exposed `best_counter`, `best_synergy`, `candidate_slugs`
  so the pipeline can score counters and synergies as separate states.

### Findings / decisions

- Task bodies are **named registry keys**, not inline closures, so the definition
  is auditable and the executor can validate registry coverage at construction.
- Heavy objects (the `Ontology`) never enter the execution payload — tasks keep
  it in their closure and pass only JSON-safe summaries, keeping the trace UI
  readable and the event store serializable.

### Verification (green, 2026-10-14)

- `ruff check .` clean; `mypy` clean (44 files); `pytest tests -q` → **49 passed**
  (9 new in `tests/test_workflow.py`).
- Live run on the real graph (allies `[jinx]`, enemies `[ahri, vi, leona]`):
  `succeeded`, **19 gapless events** (seq 1..19) persisted to `executions.db`,
  counter **Qiyana**, synergy **Twisted Fate** — identical to the pure engine.

### What remains

- **Milestone 5 (api)** next: FastAPI endpoints wiring the workflow + engine
  (`/api/draft/evaluate`, `/api/executions`, `/api/executions/{id}`), then
  narrator, frontend, cicd — per `feature_list.json`.

## 2026-10-14 (3) — KG review UI + deterministic engine

### What was accomplished

- **Knowledge-graph review UI** (`python -m lolcoach.cli kg-report`): a
  self-contained, offline HTML page (no server, no build) that embeds the whole
  ontology as JSON. Two views — a champion browser (search + detail tables for
  *Beats* / *Countered by* / *Synergies* with win%, games, weight, reason) and an
  interactive force-directed relation graph (type/position/min-games filters,
  drag, hover, click-through to the browser). New module
  `knowledge/report.py`; payload is XSS-escaped against `</script>` breakout.
- **Deterministic engine** (`src/lolcoach/engine/`):
  - `draft_state.py` — immutable `DraftState` (allies/enemies picks+bans, open
    roles) with `problems()`/`is_valid()` validation.
  - `trace.py` — `TraceLine(kind, source, text, delta)`, built from a relation;
    the interface between the engine and everything downstream.
  - `scoring.py` — `evaluate_draft(ontology, state)` → one counter-pick + one
    synergy-pick, de-duplicated, deterministic tie-breaks (score → slug → role
    order). Each contributing relation emits a trace.
  - `explain.py` — deterministic formatting (`describe`, `recommendation_to_dict`).
  - Purity is enforced by a test that asserts no engine module imports
    `dataprovider`/`workflow`/`server`/`agent`.

### Findings / decisions

- The engine needs **only the `Ontology`** — champion position stats and relation
  weights already live in the graph, so no separate `StatsProvider` is required
  (architecture doc updated to match).
- Counter direction is baked into the edge (winner is `source`), so counter score
  is a plain sum of edge weights; ties resolve by slug then role order.

### Verification (green, 2026-10-14)

- `ruff check .` clean; `mypy` clean (35 files); `pytest tests -q` → **40 passed**
  (12 engine, 4 report, plus the earlier suites).
- `python -m lolcoach.cli kg-report` → `data/kg-report.html` (547 KB, 173 champs /
  2,435 relations).
- Live engine check on the real graph: allies `[jinx]`, enemies
  `[ahri, vi, leona]` → counter **Qiyana** (jungle; counters Vi 53% / +0.022),
  synergy **Twisted Fate** (mid; with Jinx 56% / +0.057).

### What remains

- **Milestone 4 (workflow-state-machine)** is next, then api, narrator, frontend,
  cicd — per `feature_list.json`. No further work started pending the next go.

## 2026-10-14 (2) — knowledge graph pipeline (OP.GG → snapshot → YAML KG)

### What was accomplished

- **Decoded the OP.GG wire format.** The MCP server does **not** return JSON: each
  tool result is a column-ordered *typed repr* (`class Name: a,b,c` header lines,
  then a nested positional constructor expression). Added
  `dataprovider/typed_repr.py` — a strict tokenizer/parser (arity mismatches raise).
- Built the full Pillar-1 pipeline:
  - `dataprovider/opgg_mcp.py` — Streamable-HTTP MCP client (initialize → session
    header → `tools/call`), retry/backoff.
  - `dataprovider/store.py` — SQLite snapshot (`champions`, `champion_stats`,
    `champion_roles`, `matchups`, `synergies`, `analysis_done`, `meta`).
  - `dataprovider/fetch.py` — roster + lane-meta (membership) + per-`(champion,
    lane)` analysis; throttled and resumable via `analysis_done`.
  - `dataprovider/build_kg.py` — snapshot → `knowledge/seed/{champions,relations,patches}.yaml`.
  - `knowledge/ontology.py` + `schema/ontology.schema.json` — JSON-Schema +
    referential-integrity validated loader; `knowledge/resolve.py` — tiered name
    resolution.
  - `cli.py` — `fetch`, `build-kg`, `reset` implemented.
- Tests: 24 passing (incl. `test_typed_repr`, `test_fetch` with a fake caller,
  `test_build_kg`, `test_ontology`, `test_resolve`). `ruff` + `mypy` clean.

### Findings / decisions

- Tool inputs are strict: `position` and `game_mode` are required; `desired_output_fields`
  is a closed set of exact paths.
- The roster mixes 173 real champions with ~77 alternate-mode variants
  (`Jade_*`, id ≥ 60000); the fetch **excludes** them.
- Counter direction is derived from the measured win rate, not the tool's
  `strong`/`weak` label, so both perspectives collapse to one edge at the winner.
  Edge weight = win-rate delta × sample-size confidence; edges below 200 games or
  1% delta are dropped.
- A real bug fixed: `call_tool` returns the tool *root*; `_store_analysis` needed
  the inner `data` object. Added `test_fetch` to lock this in.

### Verification (green, 2026-10-14)

- `ruff check .` → clean; `mypy src` → 19 files clean; `pytest tests -q` → 24 passed.
- **Full live pull completed:** 268/268 pairs, **0 errors**. Snapshot: 173 champions,
  268 stat rows, 982 role rows, **1,596 matchups, 3,216 synergies**. Re-running the
  fetch skipped all 268 pairs → resumability verified end-to-end.
- `python -m lolcoach.cli build-kg --patch 14.20` → **173 champions, 2,435 relations
  (754 counters, 1,681 synergies)** in `knowledge/seed/{champions,relations,patches}.yaml`
  (relations.yaml ≈ 498 KB). `load_ontology()` passes schema + referential checks;
  spot-checked edges read sensibly (e.g. "Xayah beats Ezreal: 57.0% over 2,665 games";
  "Blitzcrank + Jinx: 56.0% over 1,910 games"); `NameResolver` handles aliases.
- Live smoke `fetch --limit 5` earlier also confirmed per-lane membership + resumable skip.

### What remains (STOPPED HERE for user review)

- **Milestone 2 (knowledge-graph-pipeline) is complete → marked `pass`.**
- No further milestone started. Milestone 3 (deterministic-engine) onward awaits the
  user's review of this step, per instruction.
- Minor open item for review: synergy edges are emitted **per position** (the same
  champion pair can appear at `mid` and `adc`); worth confirming that is wanted before
  the engine consumes it.

## 2026-10-14 — harness filled in; backend scaffold beginning

### What was accomplished

- **Verified the data source (the project's biggest open question).** The OP.GG
  MCP server at `https://mcp-api.op.gg/mcp` works: Streamable-HTTP, JSON-RPC POST,
  **no auth**, session id returned in the `Mcp-Session-Id` response header
  (`initialize` → `notifications/initialized` → `tools/call`, protocol 2025-06-18).
  `lol_get_champion_analysis` returns real data. `position` is **required** and must
  be a real lane (top/jungle/mid/adc/support) — `"all"` and omission are both rejected.
- Confirmed the data shapes we need: `data.summary.average_stats.{win_rate,pick_rate,ban_rate,tier,rank}`,
  `data.summary.positions[].{name,roles[]}`, `data.strong_counters[]` (matchups the
  champion **wins**; `my_win_rate` = champion's rate), `data.weak_counters[]` (champions
  that beat it), `data.synergies.{top,mid,jungle,adc,support}[].{synergy_champion_name,win_rate,play}`.
- **Filled in the harness** (this repo's spec-driven scaffold): `AGENTS.md`,
  `README.md`, `docs/ARCHITECTURE.md`, `docs/PRODUCT.md`, `docs/RELIABILITY.md`,
  `feature_list.json` (9 milestone entries), `clean-state-checklist.md`,
  `scripts/benchmark.sh`, `scripts/cleanup-scanner.sh`, `init.sh`.
- Backend scaffold files created: `pyproject.toml`, `src/lolcoach/__init__.py`,
  `src/lolcoach/config.py` (`Settings` + `get_settings()`), `src/lolcoach/logging_setup.py`
  (structured JSON logging).

### Decisions made

- **OP.GG-only knowledge graph** (per user). Edge "reasons" are statistical, not
  authored: traces cite measured evidence like "beats X: 57.3% over 1,234 games".
- **In-house Step-Functions-style executor**, deliberately not LangChain/LangGraph
  (per user) — the traceability requirement is the reason it exists.
- Feature scope: **core only** (no vision, Steam/Riot login, RAG, quiz, post-game,
  ban recommendations).
- Data breadth: **all champions, throttled + resumable**.
- CI/CD: **CI + Dockerfile**.
- Product invariant restated as a hard boundary: **the LLM never decides picks**.

### Verification (all green, 2026-10-14)

`bash init.sh` on Python 3.14.7:

- `ruff check .` → All checks passed!
- `mypy src` → Success: no issues found in 7 source files
- `pytest tests -q` → 3 passed (`test_health.py`, `test_logging_setup.py`)
- Boot smoke: `create_app()` + `GET /api/health` → `{"status":"ok","service":"lol-coach","version":"0.1.0"}`, structured JSON log lines emitted.

### What remains

- **Open design question:** champion→role membership. A per-champion
  `lol_get_champion_analysis` call needs a position, so pulling all champions naively
  is expensive. Plan: use `lol_list_lane_meta_champions` (schema not yet retrieved —
  the probe was interrupted) or each champion's primary position to avoid ~850 calls.
  Resolve before finalizing `dataprovider/fetch.py`.
- Milestone 2 (knowledge-graph-pipeline) onward, per `feature_list.json`.

### Files modified

- Harness: `AGENTS.md`, `README.md`, `docs/*`, `feature_list.json`,
  `clean-state-checklist.md`, `session-handoff.md`, `init.sh`, `scripts/*`.
- Backend: `pyproject.toml`, `src/lolcoach/{__init__,config,logging_setup}.py`.
