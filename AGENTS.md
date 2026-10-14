# LoL Coach — Agent Guide

The shared source of truth for every coding agent working in this repository.

## Startup Rules

Before writing any code, complete these steps in order:

1. **Read this file completely.** It defines the boundaries and conventions for this project.
2. **Read `docs/ARCHITECTURE.md`** to understand the LoL Coach layer structure.
3. **Read `docs/PRODUCT.md`** to understand the feature requirements.
4. **Read `docs/RELIABILITY.md`** to understand logging, observability, and clean state.
5. **Run `bash init.sh`** to verify the project builds cleanly. If it fails, fix build errors before proceeding.
6. **Read `feature_list.json`** to see the current state of the features.

## Docs Hierarchy

The `docs/` directory is organized for agent readability:

```
docs/
    ARCHITECTURE.md -- LoL Coach layers, data flow, state machine, full pipeline
    PRODUCT.md      -- Feature requirements and user facing behavior
    RELIABILITY.md  -- Logging, observability, clean state, benchmarking
```

When adding new features, update the relevant doc before writing code.

## Project: LoL Coach

A neurosymbolic League of Legends drafting coach. A deterministic Python engine
evaluates a live pick/ban state against a YAML knowledge graph and an OP.GG-derived
SQLite snapshot, and returns exactly **one counter-pick** and **one synergy-pick**,
each with source-tagged traces. An LLM narrator rephrases only those traces.

Reference implementation (Dota 2 sibling, read for ideas only): `../dota-buddy`.

**Product invariant above all: the LLM never decides picks.** The deterministic
engine decides, the knowledge/data layer explains, the narrator only rephrases tool
results. New capability = a new engine computation with traces, never a prompt
permission.

## Layering & boundaries

```
src/lolcoach/
    engine/       PURE. No I/O, no network, no clock, no imports from the other layers.
                  Takes an Ontology + a StatsProvider by dependency injection.
    knowledge/    The YAML knowledge graph: load, JSON-Schema + referential validation.
    dataprovider/ The ONLY layer that talks to the network (OP.GG MCP) and the ONLY
                  layer that writes the stats snapshot.
    workflow/     The in-house Step-Functions-style executor. The ONLY layer that
                  persists execution history.
    agent/        The LangChain narrator. Reads engine traces, produces prose.
    server/       FastAPI. Wires the workflow + engine + narrator to HTTP.
```

- `engine/**` must not import from `dataprovider`, `workflow`, `server`, or `agent`.
- `dataprovider/**` must not import from `server`.
- The narrator may not assert any fact or number not present in the engine traces.

**OP.GG data shape (important):** the OP.GG MCP server does *not* return JSON. Each
tool result is a column-ordered "typed repr" (`class Name: a,b,c` header lines,
then a nested positional constructor expression). `dataprovider/typed_repr.py`
parses it. Tool inputs are also strict: `position` and `game_mode` are required,
and `desired_output_fields` is a closed set of exact paths (`[]` marks arrays).
The roster mixes real champions with alternate-mode variants (`Jade_*`, id ≥ 60000);
the fetch excludes those.

## Conventions

- Type hints everywhere; `mypy` (strict) must pass. No `Any` without a comment explaining why.
- Logging via module-level loggers: `logger = logging.getLogger(__name__)`.
- INFO for significant events (snapshot built, workflow succeeded).
- WARNING for missing but non-critical data (champion not in the graph, fallback used).
- ERROR for failures. Inside `except` blocks, use `logger.exception(...)` so the traceback is captured.
- Logging is configured once in `src/lolcoach/main.py` — never call `basicConfig` in domain modules.
- `ruff check .` must be clean.

## Definition of Done

A feature is "done" when all of the following are true:

1. Python compiles and imports without errors.
2. `bash init.sh` passes (lint, types, tests).
3. The feature appears in `feature_list.json` with status `"pass"` and evidence.
4. The code respects the LoL Coach boundaries defined above.
5. No errors in the structured JSON logs during normal operation.
6. Structured logging covers all service operations.
7. `docs/ARCHITECTURE.md` and/or `docs/PRODUCT.md` are updated.
8. `clean-state-checklist.md` passes all checks.

## Working with the Feature List

`feature_list.json` is the source of truth for project progress:

- Each feature has a `status`: `"pass"`, `"fail"`, or `"not-started"`.
- When implementing a feature, update its status to `"pass"` with evidence (test names, commands, outputs).
- If a feature is blocked, set status to `"fail"` with a reason.
- Never delete features from the list.

## Session Handoff

When resuming work, read `session-handoff.md` for context from a previous session.
When finishing a session, update it with:

- What was accomplished
- What remains
- Any blockers or decisions made
- Files that were modified
- Benchmark results if applicable

## Clean State

Before each major testing cycle:

1. Run `bash scripts/cleanup-scanner.sh` to check for stale artifacts.
2. Verify `clean-state-checklist.md` passes.
3. Run `bash scripts/benchmark.sh` to measure performance.
