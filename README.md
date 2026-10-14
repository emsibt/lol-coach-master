# LoL Coach

A neurosymbolic League of Legends drafting coach. The deterministic engine
decides, the knowledge graph explains, the agent translates.

> **The core design decision, stated out loud:** the LLM never decides picks.
> A deterministic Python engine scores the current pick/ban state against a YAML
> knowledge graph and an OP.GG-derived SQLite snapshot, and returns exactly one
> counter-pick and one synergy-pick. Every point of every score carries a
> source-tagged trace, and the model may only rephrase those traces.

## Architecture

Layers, one invariant:

```
  AGENT (LangChain: Claude or OpenAI)   adapts fuzzy input, orchestrates tools,
        |  tools + traces in            narrates results. Never decides.
  ENGINE (pure Python, deterministic)   counter-pick + synergy-pick, every point traced.
        |  reads
  DATA   YAML knowledge graph (champions, counter/synergy edges, patch)
         SQLite snapshot (OP.GG: win/pick/ban, head-to-head matchups, synergies)
  WORKFLOW (in-house, Step-Functions-like)  runs the draft pipeline AND the agent's
         tool-loop, recording every state transition to an inspectable history.
```

The chat is the front door: you type `jinx vs their zeri` (or paste a draft
screenshot), and the agent resolves it to the engine's strict input, calls the
deterministic tools, and answers in coach voice. There is no path from the
language model to a draft fact except through the engine's tool results and
traces — a grounding check discards any reply that names a champion no tool
produced. See `docs/ARCHITECTURE.md` for the full picture.

## Quickstart

```bash
# backend
pip install -e ".[dev]"

# offline data backbone (run once; throttled + resumable)
python -m lolcoach.cli fetch --limit 40      # OP.GG pull -> data/snapshot.db
python -m lolcoach.cli build-kg              # snapshot -> knowledge/seed/*.yaml
python -m lolcoach.cli kg-report             # review the graph in a browser

# run
cp .env.example .env                          # optional: add a model key
uvicorn lolcoach.main:app --reload            # API on http://127.0.0.1:8000

# frontend
cd frontend && pnpm install && pnpm dev       # http://localhost:5173 (chat at /app)

# verify
bash init.sh                                  # ruff + mypy + pytest
python -m lolcoach.cli demo                   # flagship scenario in the terminal
```

## Model keys (optional)

The agent works with **or without** a key. Put the key in `.env` (copy from
`.env.example`); the app loads `.env` with `override=True`, so the value there is
always the one used — a stale `ANTHROPIC_API_KEY` in your shell will not win.

```bash
LOL_COACH_LLM_PROVIDER=anthropic   # or: openai
ANTHROPIC_API_KEY=sk-...           # key for the selected provider
# OPENAI_API_KEY=sk-...
# LOL_COACH_LLM_MODEL=...          # defaults: claude-sonnet-5 / gpt-4o
# LOL_COACH_LLM_BASE_URL=...       # endpoint override; otherwise the real API
# LOL_COACH_ENABLE_VISION=true     # image input (needs a key)
# LOL_COACH_MAX_TOOL_CALLS=8       # agent tool-loop budget
```

The endpoint is always sent explicitly, so ambient `ANTHROPIC_BASE_URL` /
`OPENAI_BASE_URL` cannot silently redirect the app's credentials. Without a key the
chat still works end to end: a deterministic planner drives the same tools and a
grounded offline summary answers, so nothing in the engine, the traces, or the
Trace Inspector depends on the model.

## API

| Route | Purpose |
|-------|---------|
| `POST /api/agent/chat` | the chat front door: `{message, board?, image?, turns?}` → reply + tool calls + board |
| `POST /api/draft/evaluate` | evaluate a board directly (the draft-eval workflow) |
| `POST /api/chat` | narrate one execution's recommendation |
| `GET /api/executions`, `GET /api/executions/{id}` | the Trace Inspector's history, including the agent's tool-loop |
| `GET /api/champions`, `GET /api/health` | roster + liveness |

## Stack

Python 3.11 · FastAPI · Pydantic v2 · SQLite (stdlib) · LangChain
(`langchain-anthropic` / `langchain-openai`, loaded lazily) · React 18 + Vite +
TypeScript + Tailwind.
Data source: the [OP.GG MCP server](https://github.com/opgginc/opgg-mcp)
(Streamable HTTP, no auth). CI: GitHub Actions; containers via Dockerfile.

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layers, state machine, data flow
- [`docs/PRODUCT.md`](docs/PRODUCT.md) — features and non-goals
- [`docs/RELIABILITY.md`](docs/RELIABILITY.md) — logging, clean state, benchmarking
- [`AGENTS.md`](AGENTS.md) — boundaries and conventions for coding agents
