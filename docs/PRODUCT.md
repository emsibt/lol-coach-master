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
`suggest_pick`.

Chat requires a model. If the configured provider fails at the **Think** step —
no key, a rejected request, a timeout — the question is marked **failed** and you
get an error, not an answer. Nothing is written from the model's own League
knowledge and nothing is faked offline. The engine, the draft board, and the
Trace Inspector keep working without a key; only the chat answer needs one
(without any key configured the endpoint responds `503` before starting a run).

### Chat history
The chat is organized into **sessions**, like ChatGPT or Gemini. A session list
sits beside the chat: start a new one, reopen an old one, rename it (double-click
its title), or delete it. Each session remembers its whole conversation and the
draft board it was working from, so reopening it drops you back exactly where you
left off. A session is auto-titled from your first question.

Every session holds at most **5 questions** (configurable via
`LOL_COACH_MAX_QUESTIONS`). The remaining count is shown in the chat header; once
you hit the limit the composer locks and you start a new chat. This keeps each
conversation focused — and keeps the agent's context small and sharp.

The session's conversation *is* the agent's memory. Earlier turns are replayed to
the agent as context, it can re-read them on demand (`recall_history`), and it can
keep short notes to itself (`remember`) — a board detail or a constraint you
stated — that it re-reads on every later turn in that session.

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

### Champion portraits
Champions are shown with their portrait wherever a draft is on screen — the
champion grid, the board summary, the pick slots and ban chips, and next to a
recommendation. Portraits are derived from Data Dragon, the public CDN of the game
client, using the champion's `key` and the version recorded in the knowledge graph
(`ddragon_version`, surfaced on `GET /api/health`). Nothing is fetched by the
server: the URL is built in the browser. If the version is missing or an image
404s, the avatar degrades to the champion's initials — the draft never renders
blank.

### Tips, abilities, and builds
Ask the coach about a champion and it can look up the reference material a player
actually wants mid-draft: **tips** (how to play with and against them), **abilities**
(passive + Q/W/E/R with their descriptions), and a **build** — starting items,
boots, core items, runes, and skill order — for a given role.

This is *fetched structured data*, materialized into the offline pipeline the same
way the statistical snapshot is: the item catalog and every champion's tips,
abilities, and builds are pulled once from OP.GG, snapshotted, written to YAML by
`build-kg`, and read from that YAML at runtime. Answers are therefore instant and
work with no network. Item names in a build are resolved through the materialized
catalog, so the coach quotes the real item, not a guess.

Reference data is **evidence, never a decision**: the tips tool and the build tool
are the only new sources they can ground on, and neither can name a pick. The one
counter-pick and one synergy-pick still come only from `suggest_pick`.

### Rank lookups
Ask "what's my rank?" with a Riot ID (`Name#TAG`) and the coach reports that
player's current tier, division, LP, and win/loss record — the identity facts a
player asks for before deciding what to play. The lookup is **live**: rank moves,
so it cannot be checked in like the snapshot. It is the only thing the coach
fetches during a chat, and only when asked.

A profile is a *player* fact, not a *draft* fact, so it reports rank/LP/record
**only** — never a champion pool, and never a pick. Because it sources no
champion, it can never license the coach to name one.

### Lane matchup guides
Ask how one champion lanes against a specific opponent in a role — "how does
Galio do against Ahri mid?" — and the coach returns the written matchup tip, which
champion has the lane and solo-kill advantage, the recommended play style, and the
champion's win rate in that lane. This is live too, and the only other network
call the coach makes mid-run.

Like the tips, the guide is **evidence for a matchup you named**: it declares the
pair as its sources, so it can never widen what the coach is allowed to talk about
beyond the two champions you asked about.

Both live lookups are defensive by construction: a short timeout, one retry, a
politeness gate, and a short-lived cache. If OP.GG is unreachable, that tool call
is recorded as failed and the run still finishes — a dead upstream never breaks a
chat.

### Trace Inspector
Because both the draft evaluation and the agent's tool-loop run as an in-house
state machine (AWS-Step-Functions style), every run is recorded as an ordered
history of states, inputs, outputs, and durations. The Trace Inspector renders any
run as an **n8n-style node graph** of the whole workflow: each state is a node
showing whether it succeeded, failed, is still running, or was never reached; the
path the run actually took is highlighted and the branches it skipped are dimmed.
Loops (the agent's `Think ⇄ RunTool`) show as a visit count on the node. Click a
node and the side panel gives **Input**, **Output**, and **Logs** tabs — the Logs
tab is the exact `{timestamp, level, logger, message, data}` records the step's
code emitted while it ran, so you can see what happened *inside* a state, not just
its result. A **Rerun** button replays a past execution's input through the same
workflow and opens the fresh run beside it (the original is never modified). This
is the tool for "why did the coach say that?", "which tool did the agent call?",
and "why did it break?" — down to the log line.

### Knowledge graph + statistical snapshot
A YAML knowledge graph (champions, counter/synergy relations, patch) is generated
from an OP.GG-derived SQLite snapshot. The graph is data-derived, not hand-authored:
edge weights come from measured win-rate deltas and sample sizes, and each edge
carries an auto-generated statistical explanation.

## Non-goals

Explicitly out of scope for this build:

- **Steam/Riot login** — no account linking, no stored identity, no personal match
  history. Looking up a *public* Riot ID's rank on request is in scope (see *Rank
  lookups*); that is a read-only lookup of public data, not a login, and nothing
  about the player is persisted.
- **RAG over patch notes / strategy documents** — explanations cite the statistical
  snapshot, not retrieved prose. The tips and abilities added later are *fetched
  structured data* materialized the same way the snapshot is (fetch → snapshot →
  YAML → read), not a document index: nothing is chunked, embedded, or retrieved,
  and the coach quotes a field, never a passage it found.
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
  a pasted draft image and reconstruct the board (requires a model provider and
  `LOL_COACH_ENABLE_VISION`; without a provider, chat is unavailable and returns
  `503`).
- **LLM orchestration** — a multi-step tool-loop (adapt → orchestrate → narrate)
  over the deterministic tools, traced as a workflow so its journey is auditable.
- **Materialized reference data** — champion tips, abilities, lore, builds, and the
  item catalog, fetched once and read from YAML at runtime (see *Tips, abilities,
  and builds*). Advisory evidence only: it can never produce a pick.
- **Live lookups** — a public Riot ID's rank (see *Rank lookups*) and a written
  lane matchup guide (see *Lane matchup guides*), fetched on request. Read-only,
  nothing stored, no login.
