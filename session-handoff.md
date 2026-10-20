# Session Handoff

## 2026-10-15 (8) — the maintenance agents (issue-fixer + PR-reviewer) and the admin surface

### What was accomplished

- **Two agents, as workflows.** `issue-fix` (`LoadIssue -> Investigate -> Assess ->
  Plan -> Implement -> Verify -> Verified -> OpenPullRequest -> Succeeded`, one
  `Blocked` terminal) and `pr-review` (`LoadPullRequest -> Review -> Gate -> Approve
  -> Succeeded` / `RequestChanges -> ChangesRequested`). Their phases are states in
  the in-house executor, so every result is a persisted state output the trace UI
  and admin view render — no separate observability path.
- **Every side effect behind a seam** (`github/`, `agent/{workspace,publisher,fixer,
  reviewer,coding}.py`): `GitHubClient`/`GhCliClient`/`FakeGitHubClient`,
  `Workspace`/`RepoWorkspace`/`FakeWorkspace`, `Coder`/`LlmCoder`/`ScriptedCoder`,
  `Reviewer`/`LlmReviewer`/`ScriptedReviewer`,
  `RepoPublisher`/`GitPublisher`/`RecordingPublisher`. One shared coding tool-loop
  (`agent/coding.py`) drives investigate/plan/implement/review.
- **Guardrails are deterministic checks, not prompt text.** Opt-in by the
  human-applied `agent-fix` label (LoadIssue refuses otherwise — the
  prompt-injection gate); guarded paths refused (`Implement` aborts on
  `.github/workflows` / `.env`); confined `RepoWorkspace` writes (no path escaping
  the root, no secret names `.env*`/`*.pem`/`*.key` anywhere); a command allowlist
  with `git` limited to read-only subcommands (no commit/push reachable by the
  model); the publisher only ever pushes the agent's own `agent/issue-<n>` branch.
- **The reviewer approves; a human merges.** An "approve" verdict carrying a failing
  check is sent back as `REQUEST_CHANGES`. Auto-merge is an explicit opt-in
  (`LOL_COACH_AGENT_AUTO_MERGE=false` by default) and logs a WARNING when it fires.
- **CLI**: `agent-fix` (`--issue | --all --limit`, `--repo`, `--workdir`) and
  `agent-review --pr`; both build a model client via the app's provider config and
  exit non-zero if a target did not succeed.
- **Admin separation (user directive: "do not push it together with the main
  application"; scope = *UI routes + API namespace*).** `/admin` shell in the
  frontend (`Admin.tsx`, `Agents.tsx`, `Evaluation.tsx`, `TabButton.tsx`) over a new
  `GET /api/admin/executions`, `/api/admin/executions/{id}`,
  `/api/admin/evals`, `/api/admin/evals/{run_id}`. Nothing was added to the coach
  app's own tabs. The agents are ordinary workflows, so they also render in the
  existing Trace Inspector — but rerun from there returns `400` (it cannot supply
  the checkout, model, and `gh`).
- **Evaluation history** (`eval/store.py`, `EvalRunStore`, `eval_runs` in
  `data/eval.db`): one row per suite run — kind (`chat`/`agent`), provider, model,
  timestamps, pass/fail tally, full report JSON — with the run's executions
  persisted beside it, so any scenario (pass or fail) opens as a trace. This is what
  `/admin/evaluation` lists and trends across commits.
- **The agent eval suite** (`eval/{agent_scenarios,agent_checks,agent_runner}.py`,
  `evals/agent_scenarios.yaml`, 7 scenarios): **hermetic** — the *shipped* state
  machine, task bodies, and guardrails run against the fakes with no model, token,
  network, or checkout, so CI gates it and `python -m lolcoach.cli eval-agents`
  passes anywhere. Scenarios: clean fix, vague request refused, failing tests,
  guarded-path touch, clean approval, change sent back, approve-with-failing-check
  still sent back.
- **CI** gained three jobs: `eval-agents` (uploads the report, and the eval DB as
  `agent-eval-traces` on failure), `frontend` (pnpm 12 / Node 22), `docker`;
  `eval-live` stays opt-in behind `workflow_dispatch` + `ANTHROPIC_API_KEY`.
- **Docs**: new `docs/MAINTENANCE_AGENTS.md` (both state machines, the seam table,
  every guardrail, the admin route table, and a per-concern Verification table);
  `ARCHITECTURE.md`, `PRODUCT.md`, `RELIABILITY.md` (+ the maintenance-agent suite,
  evaluation history, new log points), `README.md`, `AGENTS.md` (Definition of Done
  now requires `eval-agents` to pass), `.env.example` (the agent block), and
  `feature_list.json` (`maintenance-agents`, `agent-admin-surface` -> pass).
- **Tests** (11 new files): `test_github_seam`, `test_agent_workspace`,
  `test_agent_coding`, `test_issue_fix_workflow`, `test_pr_review_workflow`,
  `test_eval_agent_{scenarios,checks,runner}`, `test_eval_store`,
  `test_admin_api`, `test_agent_cli`.

### Decisions

- **The agents are operational tooling, not product** — hence `/api/admin/*` and
  `/admin`, kept out of the coach UI's tabs entirely.
- **Rule-based scoring, and a hermetic suite.** The chat eval needs a model; the
  agent eval deliberately does not, because that is what lets it be a CI gate
  rather than an opt-in run.
- **A `Blocked` failure terminal, not per-error states** — one place to read why a
  run refused.
- **A separate `eval_runs` table** from the executions: the execution store keeps
  what one scenario *did*, the run history keeps how the suite *scored*.

### What remains / open decisions

- **Two deferred calls for the user:** (a) four `GitHubClient` protocol methods
  (`comment_issue`, `add_labels`, `remove_label`, `pull_request_files`) have **no
  call sites** in `src/` — delete them, or keep them as the seam's declared
  contract? Removal cascades into `PullFile`, the `github/__init__.py` exports, and
  the new tests, so it was left alone. (b) Two smoke-test servers from an earlier
  session (backend `127.0.0.1:8000`, Vite `localhost:5173`, both against
  `/tmp/lolcoach-smoke/`) may still be running — stop them?
- **No live run of either agent** against real GitHub (would need a `gh`-authed
  checkout and spend tokens). The workflows, guards, and seams are covered by the
  hermetic suite and unit tests; the model-driven path has not been exercised end to
  end here.
- **Browser visual pass** of `/admin/agents` and `/admin/evaluation` — not done
  (user: "skip the browser please"). The routes are covered by `test_admin_api.py`;
  the React components are typechecked and built but not clicked through.
- Pre-existing mypy errors in `tests/test_{narrator,agent_loop,live,sessions_api}.py`
  predate this work and are untouched — `init.sh` only runs `mypy src`.

### Files modified

- Added: `src/lolcoach/github/` (`types.py`, `client.py`, `fake.py`, `__init__.py`),
  `src/lolcoach/agent/{coding,fixer,reviewer,workspace,publisher}.py`,
  `src/lolcoach/workflow/workflows/{issue_fix,pr_review}.py`,
  `src/lolcoach/eval/{agent_scenarios,agent_checks,agent_runner,store}.py`,
  `evals/agent_scenarios.yaml`, `docs/MAINTENANCE_AGENTS.md`,
  `frontend/src/Admin.tsx`,
  `frontend/src/components/{Agents,Evaluation,TabButton}.tsx`, and the 11 test files
  listed above.
- Changed: `src/lolcoach/cli.py`, `config.py`, `values.py`,
  `server/{app,schemas}.py`, `workflow/store.py`, `frontend/src/{App.tsx,api.ts,
  types.ts}`, `frontend/src/components/TraceGraph.tsx`, `.github/workflows/ci.yml`,
  `AGENTS.md`, `.env.example`, `README.md`,
  `docs/{ARCHITECTURE,PRODUCT,RELIABILITY}.md`, `feature_list.json`.

### Verification

- `ruff check .` → All checks passed!; `mypy src` → Success: no issues found in 68
  source files; `pytest tests` → **354 passed** (up from 211).
- `python -m lolcoach.cli eval-agents` → 7/7, exit 0 — the same command CI runs.
- `npm run typecheck` (tsc --noEmit) clean and `npm run build` (vite) emitted
  `dist/` — 45 modules, 210 kB JS / 20 kB CSS.

## 2026-10-15 (7) — CI/CD

### What was accomplished

- **AI observability committed** as `296bdfa "feat: AI observability"` (the work
  from the previous entry, plus a small `pyproject` extra cleanup).
- **GitHub Actions CI** (`.github/workflows/ci.yml`), on push-to-`main`, PRs, and
  manual dispatch, with `cancel-in-progress`:
  - `backend` — matrix Python **3.11 + 3.12**: `ruff check .`, `mypy src`,
    `pytest tests -q`. Hermetic, so no secrets and no token spend.
  - `frontend` — pnpm **12.9.1** + Node **22**: `pnpm install --frozen-lockfile`,
    `pnpm typecheck`, `pnpm build`.
  - `docker` — needs both, builds the two images with buildx + GHA layer cache
    (`push: false`).
- **Two Dockerfiles.**
  - `Dockerfile` (API): `python:3.12-slim`, `pip install .`, non-root user
    (uid 10001), `LOL_COACH_DATA_DIR=/data` volume, `EXPOSE 8000`, stdlib-urllib
    `HEALTHCHECK` on `/api/health`, `CMD uvicorn lolcoach.main:app`.
  - `frontend/Dockerfile`: `node:22-alpine` build stage (pnpm 12.9.1, frozen
    lockfile) → `nginx:1.27-alpine` serving `dist/`. `frontend/nginx.conf` adds
    the SPA fallback, immutable-asset caching, and an `/api/` proxy to the
    `backend` service (300s read timeout for slow agent turns).
- **`docker-compose.yml`** runs backend + frontend together (`:8000` / `:8080`)
  on a named, rebuild-surviving data volume, with an optional `.env`.
- **`.dockerignore`** at the root (keeps `README.md`, which `pyproject` reads) and
  in `frontend/`.
- Docs: `docs/ARCHITECTURE.md` (CI/CD section + two Processes rows),
  `docs/PRODUCT.md` ("Packaging and CI"), `docs/RELIABILITY.md` ("Continuous
  integration"), `README.md` (docker quick-start + stack bullet),
  `clean-state-checklist.md` (CI/CD checks), and `feature_list.json`
  (`cicd` -> **pass**).

### What remains / blocker

- **Docker is not installed on this machine**, so the image builds and
  `docker compose up` were **not executed locally** — they run in the CI `docker`
  job and on any Docker host. Everything else is verified locally (below).
- No registry/publish step: CI builds images but does not push. Add a
  `docker/login-action` + `push: true` step if a registry is wanted later.

### Files modified

- Added: `.github/workflows/ci.yml`, `Dockerfile`, `.dockerignore`,
  `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`,
  `docker-compose.yml`.
- Changed: `README.md`, `docs/{ARCHITECTURE,PRODUCT,RELIABILITY}.md`,
  `feature_list.json`, `clean-state-checklist.md`, `session-handoff.md`.

### Verification

- `bash init.sh` green (Python 3.14.7): ruff `All checks passed!`; mypy src
  `Success: no issues found in 53 source files`; pytest `211 passed`.
- `pnpm build` in `frontend/` (`tsc --noEmit && vite build`) emitted `dist/`
  (index.html + hashed CSS/JS).
- `pnpm install --frozen-lockfile` reports the lockfile up to date and leaves
  `pnpm-lock.yaml` untouched (what the changed-line count for CI confirms).
- `ci.yml` and `docker-compose.yml` parse as valid YAML.



## 2026-10-15 (6) — AI observability (Langfuse)

### What was accomplished

- **Optional Langfuse tracing at the model seam** (`src/lolcoach/observability/`).
  `tracing.trace(settings, name=..., session_id=..., tags=..., metadata=...)` wraps
  an `executor.run(...)`; it opens a Langfuse root span, sets trace-level attributes
  (`propagate_attributes`), and publishes a LangChain `CallbackHandler` on a
  `ContextVar`. `agent/llm.py::_LangChainClient.complete` reads
  `current_callbacks()` and passes them as `runnable.invoke(..., config={"callbacks":
  [...]})`, so every `Think`/narration call becomes a generation with **prompt,
  completion, tool calls, token usage, and cost** (cost computed by Langfuse from the
  model name). Because it is attached at the one seam, it is provider-neutral and
  cannot influence a decision.
- **Wired at every run boundary**: the agent-chat route (`session_id` = the chat
  session, so a conversation groups in Langfuse Sessions; tags = provider; metadata =
  provider/model/live/vision), `POST /api/chat` (narrate), the rerun route, and each
  eval scenario (`eval:<id>`).
- **Off is truly off.** Enabled only when both `LANGFUSE_PUBLIC_KEY` and
  `LANGFUSE_SECRET_KEY` are set (`tracing.configured`). Without them `trace()` is a
  no-op and the SDK is never imported; the package is an **optional** dependency
  (`pyproject` extra `observability`, also in `dev`). A Langfuse setup failure is
  logged at WARNING and skipped — observability can never fail a run.
- **Local self-host stack** `ops/langfuse/docker-compose.yml` (langfuse web+worker:4,
  postgres:17, clickhouse, redis, minio) with `LANGFUSE_INIT_*` auto-provisioning an
  org/project/user and a **fixed** dev key (`pk-lf-lolcoach-local` /
  `sk-lf-lolcoach-local`) that matches `.env.example`. Helper: `scripts/langfuse.sh
  {up|down|stop|logs|status|reset}`.

### What remains / blocker

- **Docker is not installed on this machine** (no `docker`, `docker compose`,
  `podman`, or WSL — verified), so the local Langfuse server **could not be started
  here**. The stack and helper script are checked in; run `bash scripts/langfuse.sh
  up` once Docker Desktop is installed, then set the `LANGFUSE_*` block in `.env` and
  send a chat message to see the trace. Everything else (the integration, tests, and
  a real-SDK smoke run) is verified without a server.
- Cost figures require Langfuse to know the model; an arbitrary OpenRouter slug
  records tokens but not cost until the model is priced.
- The eval suite was committed this session as `c98c0af "feat: AI Evaluation"`.

### Files modified

- Added: `src/lolcoach/observability/{__init__,tracing}.py`,
  `ops/langfuse/docker-compose.yml`, `scripts/langfuse.sh`,
  `tests/test_observability_tracing.py`.
- Changed: `src/lolcoach/agent/llm.py` (forward callbacks; Protocol `invoke(config)`),
  `src/lolcoach/config.py` (`langfuse_*`), `src/lolcoach/server/app.py` (wrap agent-chat
  / narrate / rerun), `src/lolcoach/eval/runner.py` (wrap each scenario),
  `pyproject.toml` (extra `observability`), `.env.example`, `AGENTS.md`,
  `docs/{ARCHITECTURE,PRODUCT,RELIABILITY}.md`, `README.md`, `feature_list.json`
  (`ai-observability` -> pass), `clean-state-checklist.md`.

### Verification

- `bash init.sh` green on Python 3.14.7: ruff `All checks passed!`; mypy src
  `Success: no issues found in 53 source files`; pytest `211 passed` (8 new).
- Real-SDK smoke (no server): with credentials set and `LANGFUSE_HOST=http://localhost:3000`,
  `tracing.trace(...)` constructed the client, opened the span, resolved the
  `CallbackHandler` to our registered client, and attempted a span export — the
  export timed out only because no server was listening.
- `bash scripts/langfuse.sh up` prints the "docker not found → install Docker
  Desktop" message and exits 1 (the honest state on this machine).

## 2026-10-15 (5) — agent evaluation suite

### What was accomplished

- **A golden-scenario eval harness for the chat agent** (`src/lolcoach/eval/`).
  Each scenario is a YAML record — a message, an optional board/image, and
  expectations about the *observable* outcome — in the checked-in
  `evals/scenarios.yaml` (6 scenarios: fuzzy pick request, provided-board skip,
  tips, build, matchup, smalltalk). `scenarios.py` loads + validates eagerly;
  `runner.py` executes each through the **production** agent workflow
  (`build_agent_chat`) with an injected model client, persisting runs to
  `data/eval.db`; `checks.py` scores the result; `report.py` renders text/JSON.
- **Rule-based scoring, deliberately not an LLM judge.** The whole point (and the
  product invariant) is that tool choice and grounding are checkable
  deterministically, so a verdict is reproducible. Checks: status, `tools_called`
  (ordered subsequence), `tools_not_called`, tool-call budget, resolved board,
  `require_no_unresolved`, recommendation required/forbidden, narration
  `mode` (llm vs the grounded offline fallback), grounding, reply mentions/
  excludes, and **engine fidelity** (`pick_from_engine`: a narrated pick must
  equal a fresh `evaluate_draft` over the final board).
- **Grounding check reuses the runtime guard** (`narrator.allowed_champions` /
  `unsourced_champions`) rather than re-deriving it. Note for future readers: in
  the real workflow the narrator *sanitizes* an unsourced reply before it is
  scored, so a hallucination surfaces as `mode == "offline"`, not as a raw
  unsourced reply — that is why the runtime scenarios assert on `mode: llm`.
- **CLI**: `python -m lolcoach.cli eval [--suite PATH] [--json PATH] [--live]`.
  Requires a model (clean exit 1 + message otherwise); exits non-zero if any
  scenario fails.
- **New `eval.db`** (`Settings.eval_db`, `LOL_COACH_EVAL_DB`) for eval run
  history; wired into `reset --all` so the clean-state invariant ("remove all
  data files") still holds.

### What remains

- The suite has **not been run against a live model** in this session (would spend
  real API calls). Run `python -m lolcoach.cli eval --json data/eval-report.json`
  to get a baseline pass rate and record it here.
- Optional next steps: more scenarios (vision/draft image, live rank/matchup with
  `--live`, multi-turn session memory), and a `--repeat N` for variance.

### Files modified

- Added: `src/lolcoach/eval/{__init__,scenarios,checks,runner,report}.py`,
  `evals/scenarios.yaml`, `tests/test_eval_{scenarios,checks,runner,cli}.py`.
- Changed: `src/lolcoach/cli.py` (eval + reset), `src/lolcoach/config.py`
  (`eval_db`), `.env.example`, `docs/PRODUCT.md`, `docs/RELIABILITY.md`,
  `README.md`, `feature_list.json` (`agent-evaluation` -> pass).

### Verification

- `bash init.sh` green on Python 3.14.7: ruff `All checks passed!`; mypy src
  `Success: no issues found in 51 source files`; pytest `203 passed`.

## 2026-10-15 (4) — portraits, materialized reference data, and live lookups

### What was accomplished

- **Champion portraits (derived, no fetch).** `dataprovider/build_kg.py` resolves the
  fully-qualified Data Dragon version (e.g. `14.20.1` — *not* the `14.20` patch
  label) and writes it into `patches.yaml` meta; `Ontology.ddragon_version` carries
  it, `GET /api/health` surfaces it, and `GET /api/champions` now exposes each
  champion's `key`. The browser builds
  `ddragon.leagueoflegends.com/cdn/<version>/img/champion/<key>.png`
  (`frontend/src/ddragon.ts`) and `ChampionAvatar.tsx` falls back to initials on a
  missing version or a 404, so a draft never renders blank. Wired into
  `ChampionGrid`, `DraftBoard`, `BoardSummary`, `RecommendationCard`.
- **JSON branch in the MCP client.** `opgg_mcp.py::_parse_result_text` accepts either
  the typed repr or plain JSON (`lol_list_items` returns JSON), keeping
  `ToolCaller` and every fake in the tests unchanged.
- **Materialized reference data.** New snapshot tables
  (`champion_reference`, `champion_builds`, `champion_runes`, `items`,
  `reference_done`); a resumable fetch pass (`_load_reference` + `_load_items`) that
  pulls tips/abilities/lore in batches of 10 and the item catalog in one call, plus
  the recommended build captured from the analysis call we already make; `build-kg`
  always emits `reference.yaml`, `builds.yaml`, `items.yaml` (counts logged, the
  asserted `counts` dict unchanged); a deliberately **lenient** runtime loader
  (`knowledge/reference.py`) that degrades a missing/malformed file to an empty
  `ReferenceData` with a WARNING.
- **Two materialized agent tools.** `get_champion_tips` and `get_champion_build`,
  advertised only when the data is loaded (`ToolBox.reference_tools`), so advertised
  equals available.
- **Grounding-guard hardening (the load-bearing change).** Tips and builds are
  *prose that names other champions*. `ToolResult.sources` now declares the
  champions a result is entitled to source; `ToolStep` carries it (deliberately
  **not** in `payload`, so it never reaches the model); `narrator.allowed_champions`
  whitelists only `step.sources` for the four explicit-source tools and keeps the
  existing whole-payload walk for every other tool, so `suggest_pick` /
  `explain_matchup` / `get_champion` / `set_board` behavior is unchanged. The
  system prompt gained the matching rule, and `offline_final_text` gained
  tips/build/guide/profile branches so a discarded reply still gets a grounded
  answer.
- **Live lookups (the network seam).** `LiveDataProvider` is a Protocol in
  `agent/tools.py` (so `agent/**` never imports `dataprovider`), implemented by
  `dataprovider/live.py::OpggLiveProvider` and injected by the composition root
  (`server/app.py::_live`, gated by `LOL_COACH_ENABLE_LIVE_TOOLS`).
  `get_summoner_profile` reports tier/division/LP/record for a Riot ID and **no
  champion names at all**; `get_lane_matchup_guide` returns the written tip, the
  lane/solo-kill verdicts, play style, and the champion's win rate *in that lane*,
  sourcing `(champion, opponent)`. The provider is an `lru_cache`d process
  singleton with a short timeout, one retry, a monotonic min-interval gate under a
  `threading.Lock`, and a TTL cache; a failure raises `LiveDataError`, which the
  loop turns into an `ok=False` step so a dead upstream never fails a run.
- **Docs.** `ARCHITECTURE.md` (+ *Materialized reference data*, *Reference prose and
  the grounding guard*, *Live lookups*), `PRODUCT.md` (+ four feature sections and
  two reconciled non-goals), `README.md` (tool table, env knobs), `AGENTS.md`
  (layer boundaries, the Protocol seam, the OP.GG identifier rule),
  `.env.example` (five new knobs), `feature_list.json` (+3 features, evidence
  refreshed).

### Decisions

- **Builds ride along on the analysis call.** They cost no extra request, but it
  means an *existing* snapshot has no build rows: `analysis_done` marks "the call
  succeeded", not "we captured everything the call now carries". Backfilling a
  pre-existing snapshot is a one-time local operation (clear `analysis_done` and
  re-run `fetch`), not a code path.
- **Summoner spells and stat shards are not materialized.** OP.GG returns bare ids
  for both (`ids_names: [4, 12]`; `stat_mod_names: [5008, …]`), which would
  materialize as meaningless numbers. Fourth/fifth/sixth items are likewise out.
- **The profile reports no champion pool.** The plan's Context mentioned "rank +
  hero pool", but its Sources line narrowed the tool to "rank/LP/level — never a
  pick". Surfacing a pool would force a choice between whitelisting the player's
  champions (which are not draft facts) and discarding every answer that names
  them, so the payload carries rank/LP/record only. Easy to revisit if wanted.
- **`sources` lives on `ToolStep`, not in `payload`** — a slug list in the
  model-facing JSON is an invitation to echo it.
- **A fresh MCP session per live call** (rather than one long-lived client): the
  session id is server state and FastAPI runs these endpoints on a threadpool; the
  cache absorbs repeats, so the extra `initialize` is not on the hot path.
- **The seed files are regenerated, not hand-edited.** The committed
  `champions.yaml`/`relations.yaml` moved from 2,435 to 3,052 relations because the
  snapshot was re-analysed against fresh upstream data; the reference/builds/items
  files are new. All generated by the same `build-kg`.

### Verification (green, 2026-10-15)

- `ruff check .` clean; `mypy src` "no issues found in **46** source files";
  `pytest tests` → **181 passed** (up from 143). New/changed suites:
  `test_text` (the OP.GG identifier rule), `test_reference` (lenient loader),
  `test_live` (shaping, cache, gate, singleton, no champion in the profile payload),
  plus extensions to `test_fetch`, `test_store`, `test_build_kg`, `test_agent_tools`,
  `test_agent_loop`, `test_narrator`, `test_api`.
- **Real data, end to end (live OP.GG, temp dirs):** fetch → build-kg →
  `load_reference` produced **173 champions / 268 builds / 315 items**, then the
  same pipeline was run against the real snapshot with **0 errors**:
  `fetched: 173 champions … 315 items` and
  `build-kg --patch 14.20` → `173 champions, 3052 relations (998 counters, 2054
  synergies)`, reference 173 / builds 268 / items 315. `load_ontology()` passes
  schema + referential validation; the full test suite is green against the
  regenerated seed.
- **Two real bugs found and fixed by running it for real:**
  1. Champion details were addressed by the *Riot key*, which OP.GG rejects for
     names like `Dr. Mundo` (`DRMUNDO`); the accepted form is the display name with
     punctuation dropped and spaces → `_` (`DR_MUNDO`, but `CHOGATH`). This silently
     lost 70 of 173 champions on the first pull. `text.upper_snake` now implements
     the real rule and has its own test.
  2. Summoner spells / stat shards would have materialized as bare numbers.
- Live-tool payload shapes verified against the real API (the raw guide response is
  ~86 KB; the shaped payload is ~9 fields).

### What remains

- **Browser visual pass** of the portraits (grid/board/recommendation/icons + the
  404 → initials fallback) — still the outstanding human check, carried over.
- **Live end-to-end chat through a real model** for the new tools (a rank question
  and a matchup-guide question): the tools, the guard, and the offline fallbacks are
  covered by tests, but no chat turn has been run against a provider this session.
- **cicd** is still the only `not-started` feature.

## 2026-10-15 (3) — chat history sessions + agent memory

### What was accomplished

- **Chat sessions (ChatGPT/Gemini style).** A new storage layer, `lolcoach/chat/`,
  persists conversations to a third database, `data/chat.db` (`sessions`,
  `session_messages`, `session_notes`) — kept apart from the immutable
  `executions.db` and the replaceable `snapshot.db`. `ChatStore` supports
  create/list/get/rename/delete, message append, notes, and counts.
- **5-question cap per session.** `LOL_COACH_MAX_QUESTIONS` (default 5). Enforced
  atomically in `ChatStore.append_question` (raises `SessionLimitReached`) and at
  the route (HTTP **409**). The slot is consumed when the question is asked, so the
  cap bounds requests, not answers.
- **Session history is the agent's memory.** `POST /api/agent/chat` gained
  `session_id`: it loads the session's prior turns and notes, replays the turns as
  conversation context, and persists each user/assistant turn with its run metadata
  (mode, tool calls, picks, execution id). The board is stored on the session too.
- **Memory tools.** `chat/memory.py` defines `SessionMemory` plus two tools —
  `recall_history` and `remember` — offered only when a run is session-bound
  (`tool_specs(include_memory=True)`), dispatched in `workflow/workflows/agent_chat.py`.
  Notes re-inject as a system message on every turn. The workflow stays DB-free:
  the **server** persists returned notes.
- **API + UI.** Routes `POST/GET /api/sessions`, `GET/PATCH/DELETE
  /api/sessions/{id}`. The frontend gained `SessionSidebar` (new/open/rename/delete,
  per-session question counter) and a session-scoped, controlled `ChatPanel` with a
  remaining-questions header; `App.tsx` owns session/message state.
- **Docs.** `ARCHITECTURE.md` (chat layer, data dir, sessions & memory section),
  `PRODUCT.md` (chat history + cap), `README.md` (routes, agent section),
  `.env.example`, and `feature_list.json` (`chat-history` = pass).

### Decisions

- **Third database, not an executions table.** Sessions are mutable product state;
  merging them into the audit trail would make "delete a conversation" touch
  history. `chat/` is the only layer that writes `chat.db`.
- **Server owns session persistence; the workflow does not.** The agent workflow
  receives `turns`/`notes`/`memory` in its initial `data` and returns recorded
  notes in its output — the same dependency-injection style as the engine's
  ontology. This keeps `workflow/` free of any chat-store import.
- **Cap counts requests, not answers.** The question slot is consumed before the
  run, so a provider failure still costs a slot; the 503 no-key path is checked
  first and consumes nothing.
- **No auth** (explicitly out of scope): sessions are trusted as-is.

### Verification (green, 2026-10-15)

- `bash init.sh` equivalent: `ruff check .` clean; `mypy src` "no issues found in
  44 source files"; `pytest tests` **101 passed** — including the new
  `test_chat_store` (roundtrip, cap, notes, `SessionMemory`/`call_memory_tool`) and
  `test_sessions_api` (CRUD, turn persistence, 409 cap, memory reinjection, 404/503).
- Frontend: `npx tsc --noEmit` clean; `npx vite build` succeeds.

### What remains

- **Browser visual pass** of the new sidebar / session-scoped chat (carried over
  from previous entries, still the outstanding human check) — the backend paths are
  covered by tests but the UI has not been clicked through.
- **cicd** is still the only `not-started` feature.

## 2026-10-15 (2) — chat hard-fails instead of faking an answer; provider gateway headers

### What was accomplished

- **Agent chat now requires the model (user directive: "don't mock the value").**
  The offline planner was deleted outright — `agent/agent.py` lost `parse_mentions`,
  `ParsedInput`, `_offline_action`, `offline_hint` and their regexes, so
  `choose_action(context, client=…, settings=…)` runs the LLM tool-loop only. A
  missing client, or *any* provider failure, raises the new
  `LlmUnavailableError`; the executor records `STATE_FAILED` on `Think` and the
  execution ends `failed` with **no reply**. `narrate_final` likewise requires a
  client (and rejects a blank reply), so the chat never returns a fabricated
  summary. The anti-hallucination guard (`unsourced_champions`) is unchanged.
- **503 before starting.** `POST /api/agent/chat` builds the client first and, when
  no provider is configured, returns `503` without creating an execution. The
  draft-eval `narrate` path still degrades to offline text.
- **Gateway/proxy support.** `LOL_COACH_LLM_EXTRA_HEADERS` (a JSON object) is parsed
  in `config.py` (malformed JSON is logged and ignored) and forwarded as
  `default_headers` by `agent/llm.py`, which is what lets the client reach the
  local OpenCode gateway (`{"x-opencode-session":"lol-coach"}`).
- **Docs.** `ARCHITECTURE.md` (agent/narrator seams, topology diagram),
  `PRODUCT.md` (chat-requires-a-model, vision non-goal), `RELIABILITY.md` (agent log
  points), `README.md` and `.env.example` (extra-headers, the empty-key override
  trap) updated; `feature_list.json` evidence refreshed.

### Decisions

- **Whole chat fails together** — a Think failure fails the run; there is no partial
  answer path, because a chat answer with no measured basis is worse than an error.
- **No execution on 503** — a run that cannot possibly succeed should not appear in
  the Trace Inspector as noise.
- **`.env` wins over the shell** (`load_dotenv(override=True)`): an *empty*
  `ANTHROPIC_API_KEY=` line would clear an exported key, so `.env.example` says so
  explicitly. The app deliberately does **not** inherit `ANTHROPIC_BASE_URL`, so a
  Claude Code GLM setup in the shell does not reach the app — provider, model,
  base URL, and headers are all explicit `LOL_COACH_*` settings.

### Verification (green, 2026-10-15)

- `bash init.sh`: ruff clean; mypy "no issues found in 40 source files"; pytest
  **91 passed** (`test_agent_loop` 7, `test_api` 10, `test_narrator` 8,
  `test_config_env` 8).
- **Live end-to-end through the real local proxy:** `POST /api/agent/chat`
  `{"message":"jinx vs their zeri, who should we pick?"}` → HTTP 200,
  `status=succeeded`, `mode=llm`, `tool_calls=[set_board, suggest_pick,
  explain_matchup]`, grounded reply. (Before this change the same request returned a
  fabricated `mode=offline` answer.)
- Note: a server still running the old code needs a restart to pick this up.

### What remains

- **Browser visual check** of the Trace Inspector node graph (carried over from the
  previous entry) — still the outstanding human pass.
- **cicd** is still the only `not-started` feature.

## 2026-10-15 — Trace Inspector: n8n-style node graph, per-state logs, rerun

### What was accomplished

- **Per-state log capture (backend).** The executor now attaches a
  thread-filtered `logging.Handler` (`_LogCapture` in `workflow/executor.py`)
  around each state body, drains it, and persists the records onto the state's
  event as `Event.logs`. `Event.logs` → `execution_events.logs` column, added by
  `store.py::_migrate` via `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` (existing
  DBs upgrade in place; old rows read back `[]`). `_run_task`/`_run_choice` call
  `logger.exception(...)` *before* the drain so a failure's traceback is attached
  to the `STATE_FAILED` event. `STATE_ENTERED` now also carries `input`.
- **Topology API.** `Workflow.to_dict()` (`workflow/types.py`) serializes the
  static graph; `GET /api/executions/{id}` attaches it as `graph`
  (`server/app.py::_topology`). New `POST /api/executions/{id}/rerun` replays the
  stored `input` through the same definition and returns the new `execution_id`.
- **n8n-style frontend.** New `TraceGraph.tsx`: builds edges from the state
  definitions (start → start_at, `next`, choice rules + default with predicate
  labels), DFS back-edge detection, longest-path layering, SVG bezier edges,
  status-colored nodes with duration / visit count (`×N`) / log badge, and a
  detail panel with **Input / Output / Logs** tabs (JSON highlighter, log lines,
  error/traceback). `TraceInspector.tsx` rewritten as an execution sidebar (search
  + workflow/status filters, per-row duration) with a breadcrumb, hosting the
  graph and a **Rerun** button.

### Decisions

- **Topology comes from the definition, not the run** — the UI draws branches the
  run never took (dimmed), which is what makes the agent's `Think ⇄ RunTool` loop
  legible. Falls back to inferring nodes from events when `graph` is null.
- **Rerun is additive** — it creates a new execution; history is never mutated.
- Log capture is **additive** to the existing JSON console logging, not a
  replacement; no domain module calls `basicConfig`.

### Verification (2026-10-15)

- `bash init.sh` green: ruff, mypy (strict), pytest.
- `pnpm build` (tsc --noEmit + vite build) succeeds.
- Live: draft-eval graph = 11 states; agent-chat graph =
  `ParseInput → Think → Route → {Finalize | MaxSteps | RunTool(default)}` with the
  `RunTool → Think` loop and `MaxSteps` shown `not_run`; a live `ValidateDraft`
  event carried `{"level":"INFO","logger":"...draft_eval","message":"Draft
  validated","data":{"problems":0}}`; rerun returned a new `succeeded` execution.
- `logs` migration verified on a copy of the real `data/executions.db`.

### What remains

- **Browser visual check** of the new graph UI was not completed this session
  (the in-app browser page could not be shared — an environment blocker, not a
  code failure). Backend + build are verified; a human pass over the canvas is the
  outstanding item.
- **cicd** is still the only `not-started` feature.
- **Harness note (self-corrected):** the startup protocol in `AGENTS.md`
  (read the three docs, run `init.sh`, read `feature_list.json` *before* coding;
  update docs before code) was not followed while building this feature. Docs
  (`ARCHITECTURE.md`, `PRODUCT.md`, `RELIABILITY.md`), `feature_list.json`, and
  this handoff were updated after the fact to close the Definition of Done.

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
