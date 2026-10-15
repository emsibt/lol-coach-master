# Reliability -- Observability, Clean State, and Benchmarking

## Structured Logging

### Overview

All services in LoL Coach emit structured JSON log entries. This enables runtime
debugging, post-hoc analysis, and automated monitoring of application behavior.
Logging is configured exactly once, in `src/lolcoach/main.py`, by calling
`lolcoach.logging_setup.configure_logging(...)`. Domain modules never call
`logging.basicConfig`; they only create a module-level logger:

```python
logger = logging.getLogger(__name__)
```

### Log Format

Every log entry is a single-line JSON object. The formatter is
`lolcoach.logging_setup.JsonFormatter`:

```json
{
  "timestamp": "2026-10-14T12:00:00.000Z",
  "level": "INFO",
  "service": "lol-coach",
  "logger": "lolcoach.workflow.executor",
  "message": "Execution succeeded",
  "data": {
    "execution_id": "6f1a...",
    "workflow": "draft_eval",
    "duration_ms": 42
  }
}
```

- `timestamp` — UTC ISO-8601 with milliseconds, `Z` suffix.
- `level` — `DEBUG` | `INFO` | `WARNING` | `ERROR`.
- `service` — from `LOL_COACH_SERVICE` (default `lol-coach`).
- `logger` — the dotted module name.
- `message` — `logging`-style formatted message.
- `data` — optional structured fields. Pass with
  `logger.info("msg", extra={"data": {...}})`.
- `exc_info` — present only on `logger.exception(...)`; the formatted traceback.

### Log Levels

| Level | When to Use | Example |
|-------|-------------|---------|
| DEBUG | Routine data access, per-champion fetch progress | "Fetched champion analysis" |
| INFO | Significant events | "Snapshot built", "Execution succeeded" |
| WARNING | Missing but non-critical data | "Champion not found in knowledge graph", "Narrator offline (no API key)" |
| ERROR | Failures | "Execution failed", "MCP call failed after retries" |

### Service Logging Points

**dataprovider** (`lolcoach.dataprovider.*`)
- `INFO` snapshot build start/finish (champion count, patch, elapsed)
- `DEBUG` per-champion fetch success (champion, position, games)
- `INFO` reference rows / item catalog loaded (row counts)
- `WARNING` champion skipped (not found / no data / already stored)
- `WARNING` champion-details batch rejected; retrying with the key form, or giving
  up and leaving it resumable (the batch is simply not marked done)
- `ERROR` + `exception` MCP transport failure after retries exhausted

**dataprovider live** (`lolcoach.dataprovider.live`)
- `DEBUG` live lookup served from the TTL cache (tool)
- `WARNING` live lookup failed (tool, error) — then raised as `LiveDataError`, which
  the tool loop records as an `ok=False` step rather than failing the run

**knowledge** (`lolcoach.knowledge.ontology`)
- `INFO` knowledge graph loaded (champions, relations, patch, source file count)
- `WARNING` referential-integrity skip (relation endpoint/tag missing)
- `ERROR` schema validation failure (with path)

**knowledge reference** (`lolcoach.knowledge.reference`)
- `INFO` reference data loaded (champion / build / item counts)
- `WARNING` a reference file is missing, unreadable, or not a mapping; continuing
  without it (advisory data must never gate startup)
- `WARNING` malformed JSON column in the snapshot; treated as "no data"

**engine** (`lolcoach.engine.*`)
- `DEBUG` candidates scored (count, top counter score, top synergy score)
- `WARNING` no candidate fit an open role

**workflow** (`lolcoach.workflow.executor`)
- `INFO` execution started (workflow, version, execution_id)
- `DEBUG` state transition (execution_id, seq, state, status, duration_ms)
- `INFO` execution succeeded (execution_id, duration_ms)
- `ERROR` + `exception` state failed / execution failed (execution_id, state, traceback)

### Per-state log capture (Trace Inspector)

The executor additionally captures the logs emitted *inside each state body* so
the Trace Inspector can show them next to that state. While a state runs, a
thread-filtered `logging.Handler` (`_LogCapture`) is attached to the root logger;
its records are drained onto the state's event as `Event.logs` and persisted in
`execution_events.logs`. Each captured record is a JSON-safe dict
`{timestamp, level, logger, message, data?, exception?}` — the same fields as the
console line, minus `service`. This is additive: the console/JSON logging path is
unchanged, and a failing task logs via `logger.exception(...)` *before* the drain
so its traceback is both printed and attached to the `STATE_FAILED` event.

**agent** (`lolcoach.agent.*`)
- `WARNING` + `exception` (`agent.agent`) the Think step's model call failed — the
  error is attached to the `STATE_FAILED` event and the execution fails
- `WARNING` (`agent.narrator`) narration named an unsourced champion — the reply is
  dropped for the grounded offline summary
- `WARNING` (`agent.llm`) the provider or package is unavailable

**server** (`lolcoach.server.app`)
- `INFO` request handled for the mutating/draft routes (route, execution_id, duration_ms)
- `ERROR` request failed (route, status)

**eval** (`lolcoach.eval.runner`)
- `INFO` scenario evaluated (scenario, passed, execution_id)

**observability** (`lolcoach.observability.tracing`)
- `WARNING` Langfuse is configured but the package is not installed — the run
  proceeds untraced (module name)
- `WARNING` + `exception` the Langfuse LangChain integration could not be created —
  the run proceeds untraced

### Configuring Log Level

Set the `LOG_LEVEL` environment variable:

```bash
LOG_LEVEL=INFO uvicorn lolcoach.main:app      # INFO, WARNING, ERROR
LOG_LEVEL=WARNING uvicorn lolcoach.main:app   # WARNING, ERROR
LOG_LEVEL=ERROR uvicorn lolcoach.main:app     # ERROR only
```

Default: `DEBUG` (all messages).

## Clean State Management

### Purpose

Clean state management ensures testing and benchmarking start from a known, empty
state. This prevents accumulated execution history or a stale snapshot from
affecting results.

### Reset Mechanism

The runtime state lives under `data/` (gitignored). Four databases, four lifecycles:

| Path | Written by | Reset behavior |
|------|-----------|----------------|
| `data/snapshot.db` | `dataprovider/` | deleted and rebuilt by `python -m lolcoach.cli fetch` |
| `data/executions.db` | `workflow/` | deleted for a clean audit trail |
| `data/chat.db` | `chat/` | product state (sessions + memory), deleted to start a fresh conversation list |
| `data/eval.db` | `eval/` | agent-evaluation run history, deleted to clear past eval runs |

The materialized knowledge — `knowledge/seed/*.yaml`, including the reference data —
is **checked in**, not runtime state: it is regenerated by
`python -m lolcoach.cli build-kg` and reset only by re-running that build.

Reset with:

```bash
python -m lolcoach.cli reset --all            # remove all four databases
python -m lolcoach.cli reset --executions     # audit trail only (keep snapshot)
python -m lolcoach.cli reset --chat           # conversation list only
```

`reset` removes the database files (plus any `-wal`/`-shm` siblings) and recreates
the `data/` directory. It logs the removal at `INFO` and refuses to touch anything
outside `data/`.

### When to Use Clean State

- Before running benchmarks
- After a debugging session
- Before testing a new feature
- When the data directory becomes corrupted

### Clean State Verification

Use `clean-state-checklist.md` to verify:
- Build passes without errors
- Architecture boundaries are respected
- Runtime behavior is correct
- Logging output is as expected
- Data integrity is maintained

## Benchmarking

`scripts/benchmark.sh` measures the hot paths. It runs against a warm snapshot and
a reset execution store, emits structured JSON to stdout, and prints a summary:

1. **Snapshot read** — champions listed, `GET /api/champions` payload size.
2. **Engine score** — `n` draft evaluations through the pure engine (no I/O, no LLM),
   reporting p50/p95 ms.
3. **Workflow run** — `n` full `draft_eval` executions, reporting p50/p95 ms and
   rows written to `executions.db`.
4. **Narrator (offline)** — `n` offline summaries, p50/p95 ms (the LLM path is not
   benchmarked here; it is network-bound and optional).

Record the results in `session-handoff.md` so regressions are visible across sessions.

## Agent evaluation

`python -m lolcoach.cli eval` scores the chat agent against the golden suite in
`evals/scenarios.yaml` (wrapped by `scripts/eval.sh`). It is a correctness gate,
not a benchmark: each scenario runs the production agent tool-loop (with an
injected model client) and is scored by rules, so a verdict does not vary run to
run and never needs a second model.

| Exit | Meaning |
|------|---------|
| 0 | every scenario passed |
| 1 | one or more scenarios failed, or no model is configured, or the suite/graph could not be loaded |

Each run is persisted to `data/eval.db`, so a failing scenario opens in the Trace
Inspector like any other execution. `--json PATH` writes the full report
(per-scenario checks and reply) for a CI artifact; `--live` additionally
advertises the two network tools to the agent. A model is required — without one
the command prints a message and exits 1 before running anything.

## AI observability (Langfuse)

The structured logs above and the Trace Inspector record what the *workflow* did.
**Langfuse** records what the *model* did: the prompt, the completion, the tool
calls, and the token usage and cost of every call. It is optional — the app runs
identically without it.

### What is captured

Every agent run is one Langfuse **trace** (name `agent-chat`, `narrate`,
`rerun`, or `eval:<scenario>`), and every model call inside it is a **generation**
under that trace: input messages, output, tool calls, `usage` (tokens), and cost.
Chat runs carry the session as the Langfuse `session_id`, so a conversation's
turns group in the Sessions view; tags and metadata (provider, model, live-tools
and vision flags) are attached for filtering.

Cost is computed by Langfuse from the model name against its pricing table. A
model it does not know (e.g. an arbitrary OpenRouter slug) still records token
usage; only the cost figure is unavailable until the model is priced.

### Run it locally

The stack lives in `ops/langfuse/docker-compose.yml` (Langfuse web + worker,
Postgres, ClickHouse, Redis, MinIO) and needs **Docker**:

```bash
bash scripts/langfuse.sh up       # start (detached); UI on http://localhost:3000
bash scripts/langfuse.sh status   # container health
bash scripts/langfuse.sh logs     # follow logs
bash scripts/langfuse.sh down     # stop, keep data
bash scripts/langfuse.sh reset    # stop and delete all data
```

On first boot Langfuse provisions a project and a fixed API key pair, so the
block below matches the running server with nothing to copy from the UI. Log in
at http://localhost:3000 as `dev@lolcoach.local` / `lolcoach`.

```bash
# .env
LANGFUSE_PUBLIC_KEY=pk-lf-lolcoach-local
LANGFUSE_SECRET_KEY=sk-lf-lolcoach-local
LANGFUSE_HOST=http://localhost:3000
# LANGFUSE_TRACING_ENVIRONMENT=local   # optional label for traces
```

Then run the app as usual (`uvicorn lolcoach.main:app`) and send a chat message;
the trace appears in the Langfuse **Tracing** view within a few seconds. The eval
suite traces too: `python -m lolcoach.cli eval`.

### Off is truly off

Tracing is enabled only when **both** keys are set (`tracing.configured`). With
neither, `tracing.trace(...)` is a no-op on every run path, the `langfuse` SDK is
never imported, and — since it is an optional dependency — the app runs without
the package installed. If Langfuse is configured but unreachable, spans fail to
export with a background warning (visible at `LANGFUSE_DEBUG=1` or in the
exporter's own logs) and **the run still succeeds**; observability never gates a
request. A setup failure (package or LangChain integration missing) is logged at
`WARNING` and skipped.

### Verification

- `python -m pytest tests/test_observability_tracing.py -q` — the seam: a no-op
  without credentials, callbacks published and cleared per run, flush on exit
  (including on error), graceful degradation, and the model call forwarding the
  callbacks.
- With credentials set, `tests/test_observability_tracing.py` plus a live smoke
  run against the local stack confirm a trace reaches the Langfuse UI.


