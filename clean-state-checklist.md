# Clean State Checklist

Run this checklist before committing and at the end of the session.

## Build

- [ ] `pip install -e ".[dev]"` completes successfully
- [ ] `ruff check .` passes with no warnings
- [ ] `mypy src` passes (strict, no errors)
- [ ] `pytest tests -q` passes with no errors

## Architecture

- [ ] `engine/**` imports nothing from `dataprovider`, `workflow`, `server`, or `agent`
- [ ] `dataprovider/**` imports nothing from `server`
- [ ] only `dataprovider/**` writes `data/snapshot.db`
- [ ] only `workflow/**` writes `data/executions.db`
- [ ] the narrator asserts no fact/number absent from the engine traces
- [ ] `configure_logging` is called only from `src/lolcoach/main.py` (no `basicConfig` in domain modules)

## Runtime

- [ ] Application starts without errors (`uvicorn lolcoach.main:app --reload`)
- [ ] Structured JSON log output appears in console at startup
- [ ] `GET /api/health` returns `{"status": "ok"}`
- [ ] `POST /api/draft/evaluate` returns exactly one counter-pick and one synergy-pick with traces
- [ ] `GET /api/executions/{id}` returns the full ordered event history

## Logging

- [ ] All log entries are valid JSON (parseable)
- [ ] Log entries include `timestamp`, `level`, `service`, `logger`, `message`
- [ ] A failed state emits `STATE_FAILED` with a traceback and the execution ends `failed`
- [ ] No unhandled exceptions appear in the logs during a normal run

## Data Integrity

- [ ] `python -m lolcoach.cli reset --all` removes all data files (incl. `-wal`/`-shm`)
- [ ] `python -m lolcoach.cli fetch --limit 5` then `build-kg` produces a valid, schema-passing graph
- [ ] `execution_events` has no gaps in `seq` within an execution
- [ ] Every relation endpoint resolves to a known champion/role/tag

## Performance

- [ ] `bash scripts/benchmark.sh` runs without errors

## Repository

- [ ] No unintended files in git status
- [ ] No `data/*.db`, `.env`, or other sensitive files staged
- [ ] `feature_list.json` reflects actual feature status
- [ ] `session-handoff.md` updated if session is ending

## Scripts

- [ ] `bash scripts/cleanup-scanner.sh` reports no stale artifacts
- [ ] `bash scripts/benchmark.sh` completes the full task suite
- [ ] `bash init.sh` passes all verification steps
