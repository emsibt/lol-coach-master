# Product

LoL Coach answers one question during a League of Legends draft: **"what should
I pick, and why?"** It answers with exactly two recommendations — one counter-pick
and one synergy-pick — each backed by evidence you can inspect down to the raw
statistic.

## The invariant

The recommendation is computed by a deterministic Python engine. The language
model's only job is to explain an already-made decision in plain language — and,
in the chat UI, to translate what you type into the engine's strict input and
orchestrate the engine's tools. This is not a style preference; it is the
product's guarantee. An LLM that "helps decide" could hallucinate a matchup that
doesn't exist during a ranked game. Here it cannot: it only ever sees the engine's
tool results and traces, it is instructed never to introduce a fact or number
that isn't among them, and a grounding check discards any reply that names a
champion no tool produced.

## Features

### Chat with the coach
The primary view is a chat. You type in your own words — `jinx vs their zeri`,
`how does fizz do into ahri mid`, or prose — and an **agent** turns that into the
engine's strict input: it resolves shorthand, aliases, and typos to champions,
reconstructs the board, and picks the tools the question needs (set the board,
suggest a pick, explain a matchup). It can also read a pasted screenshot of the
draft. Every question fans out into real tool calls; nothing is answered from the
model's own League knowledge.

The agent is deliberately narrow, and that is the point. It exists for the three
jobs the engine can't do — **adapt** fuzzy human input, **orchestrate** multiple
tools, and **narrate** the results in coach voice. It is forbidden from deciding:
the single counter-pick and single synergy-pick come only from the engine's
`suggest_pick`. The chat degrades gracefully with no model key — a deterministic
planner drives the same tools and a grounded offline summary answers — so the
engine and traces never stop working.

### Draft board
Set up the live draft state by hand: both teams' picks and bans, and which roles
are still open on your side. Champion entry resolves forgivingly (names, aliases,
case) against the knowledge graph. What the agent parses out of a chat message is
reflected here, so you can see the board state it is working from.

### Recommendations: one counter, one synergy
On evaluate, the engine returns:
- **one counter-pick** — the best available champion against the enemy team,
  driven by head-to-head matchup win rates weighted by sample size;
- **one synergy-pick** — the best available champion to pair with your current
  allies, driven by pairwise synergy win rates.

Each is one concrete champion that fits an open role — not a ranked list to
choose from when you have seconds left on the clock.

### Traceable scores
Every point of every score carries a `TraceLine(source, text, delta)`. The `source`
points at a knowledge-graph relation, a snapshot statistic, or a named engine rule.
Open a recommendation and you can see the arithmetic: which matchup contributed
what, how many games backed it, and how confident that makes it.

### Trace Inspector
Because both the draft evaluation and the agent's tool-loop run as an in-house
state machine (AWS-Step-Functions style), every run is recorded as an ordered
history of states, inputs, outputs, and durations. The Trace Inspector lists past
executions and shows any run's full timeline — including exactly which tools the
agent called, and exactly which step failed, if one did. This is the tool for
"why did the coach say that?" and "why did it break?".

### Knowledge graph + statistical snapshot
A YAML knowledge graph (champions, counter/synergy relations, patch) is generated
from an OP.GG-derived SQLite snapshot. The graph is data-derived, not hand-authored:
edge weights come from measured win-rate deltas and sample sizes, and each edge
carries an auto-generated statistical explanation.

## Non-goals

Explicitly out of scope for this build:

- **Steam/Riot login** — no account linking or personal match history.
- **RAG over patch notes / strategy documents** — explanations cite the statistical
  snapshot, not written prose.
- **Quiz / training mode.**
- **Post-game analysis.**
- **Ban recommendations** — the engine recommends picks (counter + synergy) only.
- **The LLM making or influencing any decision** — permanently out of scope by design.
  The agent may adapt input, orchestrate tools, and narrate results; the engine
  remains the sole source of every pick.
- **Hand-authored causal reasons on edges** — the chosen OP.GG-only strategy means
  reasons are statistical, and that trade is accepted deliberately.

Deliberately **in scope** (moved out of non-goals):

- **Vision / screenshot analysis** — the agent can read champion portraits out of
  a pasted draft image and reconstruct the board (requires a model key; without
  one it asks you to type the picks).
- **LLM orchestration** — a multi-step tool-loop (adapt → orchestrate → narrate)
  over the deterministic tools, traced as a workflow so its journey is auditable.
