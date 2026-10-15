# LoL Coach

LoL Coach is a neurosymbolic drafting coach for League of Legends: a deterministic
Python engine reads a live pick/ban state and returns exactly one counter-pick and
one synergy-pick, each carrying source-tagged evidence. Every number comes from a
YAML knowledge graph and an OP.GG-derived SQLite snapshot, so a recommendation is
never invented. An LLM agent sits in front of the engine — resolving loose human
input, calling the engine's tools, and narrating the results — but it never decides
a pick.

> **The core design decision, stated out loud:** the LLM never decides picks. The
> model may only rephrase what a tool returned.

## Architecture

Layered, with one invariant: the only route from the language model to a draft
fact is through a tool result.

```
  AGENT      LangChain (OpenAI-compatible or Claude)   adapt · orchestrate · narrate
    │        tools in, traces out                      NEVER decides
  ENGINE     pure Python, deterministic                one counter-pick, one synergy-pick
    │        reads                                     every point source-tagged
  DATA       YAML knowledge graph                      champions, counter/synergy edges, patch
             SQLite snapshot (OP.GG)                   win/pick/ban, head-to-head, synergies
  WORKFLOW   in-house, Step-Functions-style            runs the draft pipeline AND the
                                                       agent's tool-loop, persisting every
                                                       transition to an inspectable history
```

The chat is the front door: you type `jinx vs their zeri` (or paste a draft
screenshot) and the agent resolves it to the engine's strict input, calls the
deterministic tools, and answers in coach voice. It can also answer the questions
*around* the pick — a champion's tips, abilities, and build (materialized offline),
and a player's rank or one specific lane matchup (fetched live). A grounding check
then discards any reply that names a champion no tool produced, so prose cannot
smuggle in a fact the engine never measured. `docs/ARCHITECTURE.md` has the full
picture.

| Route | Purpose |
|-------|---------|
| `POST /api/agent/chat` | chat front door: `{message, board?, image?, session_id?}` → reply + tool calls + board |
| `POST /api/draft/evaluate` | evaluate a board directly (the draft-eval workflow) |
| `POST /api/chat` | narrate one execution's recommendation |
| `GET /api/executions`, `GET /api/executions/{id}` | Trace Inspector history, including the agent's tool-loop |
| `POST/GET /api/sessions`, `GET/PATCH/DELETE /api/sessions/{id}` | chat history: create, list, read, rename, delete |
| `GET /api/champions`, `GET /api/health` | roster + liveness |

## Quick start

```bash
# backend
pip install -e ".[dev]"

# offline data backbone (run once; throttled + resumable)
python -m lolcoach.cli fetch --limit 40      # OP.GG pull -> data/snapshot.db
python -m lolcoach.cli build-kg              # snapshot -> knowledge/seed/*.yaml
                                             #   (graph + tips/abilities/builds/items)
python -m lolcoach.cli kg-report             # review the graph in a browser

# configure a model (required for chat — see below)
cp .env.example .env

# run
uvicorn lolcoach.main:app --reload            # API on http://127.0.0.1:8000

# frontend
cd frontend && pnpm install && pnpm dev       # http://localhost:5173 (chat at /app)

# verify
bash init.sh                                  # ruff + mypy + pytest
curl -s http://127.0.0.1:8000/api/health      # {"status":"ok",...}

# evaluate the agent (requires a model) — golden scenarios, rule-based scoring
python -m lolcoach.cli eval                   # exits non-zero if any scenario fails

# optional: AI observability — prompts, tokens, and cost per run (needs Docker)
bash scripts/langfuse.sh up                   # Langfuse UI on http://localhost:3000
```

The agent suite lives in `evals/scenarios.yaml` and drives the real chat
tool-loop, scoring tool choice, board resolution, grounding, and engine fidelity.
See [`docs/RELIABILITY.md`](docs/RELIABILITY.md#agent-evaluation).

With a Langfuse server running and `LANGFUSE_*` set in `.env`, every chat turn is
recorded as one trace — each model call's prompt, completion, tool calls, token
usage, and cost — grouped by conversation. It is opt-in and side-effect-free: with
no credentials it is a no-op and the app is unchanged. See
[`docs/RELIABILITY.md`](docs/RELIABILITY.md#ai-observability-langfuse).

### Configure a model

Chat requires a model. Put the credentials in `.env` (copied from `.env.example`);
the app loads `.env` with `override=True`, so the value there always wins over your
shell. Any OpenAI-compatible endpoint works — point `LOL_COACH_LLM_BASE_URL` at it:

```bash
LOL_COACH_LLM_PROVIDER=openai                       # or: anthropic
OPENAI_API_KEY=sk-...                               # key for the selected provider
LOL_COACH_LLM_BASE_URL=https://openrouter.ai/api/v1 # e.g. OpenRouter; omit for the real API
LOL_COACH_LLM_MODEL=deepseek/deepseek-v4.1-flash
# ANTHROPIC_API_KEY=sk-...                          # when provider=anthropic
# LOL_COACH_LLM_EXTRA_HEADERS={}                    # JSON object; headers for a gateway/proxy
# LOL_COACH_ENABLE_VISION=true                      # image input
# LOL_COACH_MAX_TOOL_CALLS=8                        # tool-loop round-trip budget
# LOL_COACH_ENABLE_LIVE_TOOLS=true                  # rank + lane-matchup lookups (network)
# LOL_COACH_LIVE_REGION=NA                          # default region for a Riot-ID lookup
```

The endpoint is always sent explicitly, so an ambient `ANTHROPIC_BASE_URL` /
`OPENAI_BASE_URL` cannot silently redirect your credentials.

Without a key, `POST /api/agent/chat` returns `503` without starting a run; if the
provider errors at the **Think** step, the execution is marked **failed** and no
answer is returned. Nothing is answered from the model's own League knowledge and
nothing is fabricated offline. The engine, the traces, and the Trace Inspector
never depend on the model.

## Agents

The agent is the orchestration layer, and it is deliberately narrow — it exists for
the three jobs the engine cannot do:

- **adapt** fuzzy human input (shorthand, typos, prose, a draft screenshot) into the
  engine's strict input;
- **orchestrate** the deterministic tools, in whatever order the question needs;
- **narrate** the tool results in coach voice.

It is forbidden from deciding: the single counter-pick and single synergy-pick come
only from `suggest_pick`. The system prompt states this, and the grounding guard
(`unsourced_champions`) enforces it structurally by discarding any reply that names
a champion absent from the tool results.

The loop runs as a first-class workflow, not a framework black box, so every step is
recorded and visible in the Trace Inspector:

```
ParseInput ─► Think ─► Route ──tool──► RunTool ─┐
                 ▲                             │
                 └─────────────────────────────┘
                          │ final
                          ▼
                      Finalize ─► Succeeded
                          │ over max_tool_calls
                          ▼
                      MaxSteps (Fail)
```

`Think` is the only place the model is consulted, and it is consulted about
orchestration, never about League. The agent is invoked through
`POST /api/agent/chat`; when a `session_id` is supplied the conversation and the
board live server-side in `data/chat.db`, and the session's transcript is the
agent's memory. Without a session the call is stateless and the client carries the
board between turns.

### Chat history & memory

Chat is organized into sessions (ChatGPT/Gemini style) with a session list in the
UI: create, reopen, rename, delete. Each session stores its transcript, board, and
the agent's notes, and is capped at **5 questions** (`LOL_COACH_MAX_QUESTIONS`) —
past the cap the API returns `409` and the UI locks the composer. The transcript
is replayed to the agent as context, and two memory tools — `recall_history` and
`remember` — let it re-read earlier turns and keep durable notes for the session.
No authentication: sessions are trusted as-is.

## Tools

The deterministic surface the agent orchestrates
(`src/lolcoach/agent/tools.py`, dispatched by `ToolBox`, schemas in `tool_specs()`):

| Tool | What it does | Notes |
|------|--------------|-------|
| `set_board` | Sets allies, enemies, both teams' bans, and open roles. Resolves loose names (aliases, typos, casing) to canonical slugs. | Returns the resolved board plus an `unresolved` list. The only fuzzy-input adapter. |
| `suggest_pick` | Runs the pure engine over the current board and returns the best counter-pick and synergy-pick with their evidence traces. | **The only source of a pick recommendation.** |
| `explain_matchup` | Returns the measured counter and synergy edges between two champions, optionally in a specific role. | Evidence only — never a recommendation. |
| `get_champion` | Returns a champion's roles and win/pick/ban rates from the snapshot. | Reference data, no scoring. |
| `get_champion_tips` | Returns a champion's tips (how to play with and against), abilities, and lore. | Materialized reference data; advertised only when loaded. |
| `get_champion_build` | Returns a champion's recommended items, runes, and skill order for a role. | Materialized reference data; advertised only when loaded. |
| `get_summoner_profile` | Returns a player's tier, division, LP, and win/loss for a Riot ID. | **Live** (network). Identity only — never a champion pool. |
| `get_lane_matchup_guide` | Returns the written tip for one champion against one named opponent in a role. | **Live** (network). Evidence for the matchup the user named. |

Every tool wraps existing engine or knowledge logic; none of them is the LLM. The
two `get_champion_*` tools read the materialized reference YAML; the two live tools
call OP.GG on demand (disable with `LOL_COACH_ENABLE_LIVE_TOOLS=false`, in which
case they are not even advertised).

Tips and matchup prose can name *other* champions ("great against Yasuo"). To keep
the grounding guard meaningful, a result declares the champions it is entitled to
source (`ToolResult.sources`) and the guard whitelists only those — a mention in
prose never widens it.

## Stack

- **Backend / API** — Python 3.11+, FastAPI, Pydantic v2, Uvicorn.
- **Agent / LLM** — LangChain (`langchain-openai`, `langchain-anthropic`, imported
  lazily so the model is optional at import time); provider-neutral client seam in
  `agent/llm.py`.
- **Data / knowledge** — YAML knowledge graph validated against a JSON Schema;
  SQLite stdlib snapshot; source is the
  [OP.GG MCP server](https://github.com/opgginc/opgg-mcp) (Streamable HTTP, no auth).
- **Workflow / observability** — in-house Step-Functions-style executor on SQLite;
  structured JSON logging; the Trace Inspector reads the persisted event history.
- **AI observability** — optional Langfuse tracing at the model seam: one trace
  per agent run with each call's prompt, completion, token usage, and cost. Lazy
  and opt-in (no credentials ⇒ no-op, no import).
- **Frontend** — React 18, Vite 6, TypeScript 5, Tailwind 3.
- **Tooling** — `pytest`, `ruff`, and strict `mypy`, all wired through `init.sh`
  (CI and container packaging are planned, not yet wired).

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layers, state machine, data flow
- [`docs/PRODUCT.md`](docs/PRODUCT.md) — features and non-goals
- [`docs/RELIABILITY.md`](docs/RELIABILITY.md) — logging, clean state, benchmarking
- [`AGENTS.md`](AGENTS.md) — boundaries and conventions for coding agents
