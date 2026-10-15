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
- `WARNING` champion skipped (not found / no data / already stored)
- `ERROR` + `exception` MCP transport failure after retries exhausted

**knowledge** (`lolcoach.knowledge.ontology`)
- `INFO` knowledge graph loaded (champions, relations, patch, source file count)
- `WARNING` referential-integrity skip (relation endpoint/tag missing)
- `ERROR` schema validation failure (with path)

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

The runtime state lives under `data/` (gitignored). Two databases, two lifecycles:

| Path | Written by | Reset behavior |
|------|-----------|----------------|
| `data/snapshot.db` | `dataprovider/` | deleted and rebuilt by `python -m lolcoach.cli fetch` |
| `data/executions.db` | `workflow/` | deleted for a clean audit trail |

Reset with:

```bash
python -m lolcoach.cli reset --all            # remove both databases
python -m lolcoach.cli reset --executions     # audit trail only (keep snapshot)
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
