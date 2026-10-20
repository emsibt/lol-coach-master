# Maintenance Agents -- the Issue Fixer and the PR Reviewer

Two agents work on this repository's own issue backlog. They are **workflows**,
not prompts: each phase is a state in the in-house executor, so every result the
agent produces is a persisted state output that the admin views and the Trace
Inspector can render.

| Agent | Workflow name | Does | Never does |
|-------|---------------|------|-----------|
| Fixer | `issue-fix` | Investigates a labelled issue, plans a fix, edits the checkout, verifies it, opens a PR | Push to the base branch, touch a guarded path, open a PR it cannot verify |
| Reviewer | `pr-review` | Reads a pull request, submits `APPROVE` or `REQUEST_CHANGES` | Merge (a human merges) |

They are operational tooling, not part of the coach product, so they are served
from `/api/admin/*` and rendered at `/admin` -- see
[Admin surface](#admin-surface-monitoring-a-run) below.

## The issue-fix loop

```
LoadIssue -> Investigate -> Assess(Choice) --not actionable--> Blocked(Fail)
                                | actionable
                                v
                   Plan -> Implement -> Verify -> Verified(Choice) --red--> Blocked
                                                      | green
                                                      v
                                      OpenPullRequest -> Succeeded
```

| State | Task | Persisted output |
|-------|------|------------------|
| `LoadIssue` | `load_issue` | the issue, the branch to create, the base branch |
| `Investigate` | `investigate` | `{is_real_issue, confidence, summary, findings[], files[]}` |
| `Assess` | *choice* | `issue_actionable`: a real issue with confidence ≥ `0.5` |
| `Plan` | `plan` | `{summary, steps[], files[], test_plan}` |
| `Implement` | `implement` | `{summary, files_changed[], notes[]}` |
| `Verify` | `verify` | `{passed, commands: [{command, exit_code, ...}]}` |
| `Verified` | *choice* | `verification_passed`: every command exited 0 |
| `OpenPullRequest` | `open_pull_request` | `{pull_request, summary}` (the body carries the investigation, plan, change, and verification) |
| `Succeeded` / `Blocked` | *terminal* | — |

`Blocked` is the single failure terminal. A run that refuses a vague issue, fails
verification, or trips a guard ends there with the reason on the `Blocked` state.

## The pr-review loop

```
LoadPullRequest -> Review -> Gate(Choice) --approved--> Approve -> Succeeded
                                  | changes requested
                                  v
                           RequestChanges -> ChangesRequested(Fail)
```

`review_approved` requires **both** the model's verdict to be `approve` *and* no
failing check to have been reported. An "approve" carrying a failing check is
sent back as `REQUEST_CHANGES` — never silently approved. The approve branch
submits a review and stops; `LOL_COACH_AGENT_AUTO_MERGE` (default `false`) is the
only path to a merge, and it logs a `WARNING` when it fires.

## Seams: why the same code runs in production and in the eval

Every side effect sits behind a protocol, so the workflow is identical whether it
is driving a real checkout or a scripted one.

| Protocol | Production | Test / evaluation |
|----------|-----------|-------------------|
| `GitHubClient` | `GhCliClient` (the `gh` CLI) | `FakeGitHubClient` (in-memory issues, PRs, reviews) |
| `Workspace` | `RepoWorkspace` (a real checkout) | `FakeWorkspace` (in-memory tree + scripted command exits) |
| `Coder` | `LlmCoder` (a model) | `ScriptedCoder` (canned investigation/plan/implementation) |
| `Reviewer` | `LlmReviewer` (a model) | `ScriptedReviewer` (a canned verdict) |
| `RepoPublisher` | `GitPublisher` (commit + push) | `RecordingPublisher` (records the branch, runs no git) |

This is what makes `python -m lolcoach.cli eval-agents` hermetic: the *shipped*
state machine, task bodies, and guardrails run with no model, token, network, or
checkout.

## Guardrails

The guardrails are deterministic checks in the task bodies, not prompt
instructions. A prompt can be argued with; a resolved-path check cannot.

- **Opt-in by label.** `LoadIssue` refuses an issue that does not carry
  `LOL_COACH_AGENT_ISSUE_LABEL` (default `agent-fix`). Only a human applies the
  label, which is the gate against acting on an issue body the agent merely read.
  A prompt-injection attempt in an untrusted issue never reaches a model.
- **Guarded paths.** `Implement` aborts the run if any changed file is under
  `LOL_COACH_AGENT_DENIED_PATHS` (default `.github/workflows`, `.env`).
- **Confined writes.** `RepoWorkspace` refuses reads and writes that resolve
  outside the workspace root, and refuses secret names (`.env*`, `*.pem`, `*.key`)
  anywhere in the tree.
- **No shell.** The workspace's command allowlist admits only read/verify programs
  (`python`, `pytest`, `ruff`, `mypy`, `cat`, `ls`, `grep`, …); `git` is admitted
  only for read-only subcommands. Committing and pushing are not available to the
  model at all — the workflow calls the publisher seam.
- **Never the base branch.** The publisher pushes the agent's own
  `<prefix>-<number>` branch. Nothing in the loop can push, force-push, or delete
  the base branch.
- **No duplicate work.** `LoadIssue` fails the run if a pull request already
  targets the branch.
- **A human merges.** The reviewer submits a review and stops.

## Running it

```bash
# the fixer: one issue, or every issue carrying the label
python -m lolcoach.cli agent-fix --issue 12 --repo owner/name --workdir /path/to/checkout
python -m lolcoach.cli agent-fix --all --repo owner/name --workdir /path/to/checkout --limit 3

# the reviewer
python -m lolcoach.cli agent-review --pr 34 --repo owner/name --workdir /path/to/checkout
```

Both need an LLM (`LOL_COACH_LLM_PROVIDER` plus its key) and the `gh` CLI
authenticated. A non-zero exit means at least one target did not succeed; the
trace says why.

## Admin surface (monitoring a run)

The agents are not in the coach UI. Their surface is a separate set of routes:

| Route | Shows |
|-------|-------|
| `/admin/agents` | every `issue-fix` and `pr-review` run, newest first, with the investigation, plan, change, verification, PR, or review verdict for the selected run, above its full state graph |
| `/admin/evaluation` | the evaluation-run history; select a run to see every scenario's pass/fail and each check, and open any scenario's execution as a trace inline |

| API route | Returns |
|-----------|---------|
| `GET /api/admin/executions?limit&workflow` | agent-loop runs (`issue-fix` / `pr-review`), newest first |
| `GET /api/admin/executions/{execution_id}` | one run: input, output, events, and graph. Falls back to the eval database, where evaluation executions are persisted |
| `GET /api/admin/evals?limit&kind` | evaluation-run history (`kind=agent` or `chat`) |
| `GET /api/admin/evals/{run_id}` | one run with its per-scenario verdicts and checks |

Because both are ordinary workflows, an agent run also renders in the app's Trace
Inspector alongside every other execution. Rerunning one from there is refused
with `400`, though: `POST /api/executions/{id}/rerun` cannot supply the checkout,
model, and `gh` a run needs, so it points at the CLI instead.

A run is traced in Langfuse under the workflow's name (`issue-fix` /
`pr-review`), tagged `agent`, the workflow name, and the provider, with the issue
or PR number and repo in the metadata — so the model's calls for one run group
together.

## Evaluating it

`evals/agent_scenarios.yaml` holds seven scenarios: a clean fix, a vague request
that must be refused, a fix whose tests fail, a fix that touches a guarded path,
a clean approval, a change that must be sent back, and an "approve" verdict that
must still be sent back because it carries a failing check.

```bash
python -m lolcoach.cli eval-agents --json agent-eval.json
```

Each scenario runs the production workflow against the fakes and is scored by
rules (exit state, visited states, branch, PR linkage, published-or-not, changed
files, whether anything was written, verification, error text). It is a
correctness gate, not a benchmark: the verdict does not vary run to run.

| Exit | Meaning |
|------|---------|
| 0 | all seven scenarios passed |
| 1 | one or more failed, or the scenario file could not be loaded |

| Flag | Effect |
|------|--------|
| `--suite PATH` | use a different scenario file (default `evals/agent_scenarios.yaml`) |
| `--json PATH` | write the full report for a CI artifact |

Every run is recorded in the evaluation history (`data/eval.db`), so
`/admin/evaluation` lists it; each scenario's execution is persisted beside it and
opens in the admin trace panel. The suite is hermetic, so CI runs it on every
change (the `eval-agents` job) — see `docs/RELIABILITY.md`.

## Verification

The unit tests cover the same seams and guardrails the eval exercises, one file
per concern:

| Area | Test |
|------|------|
| the GitHub types, fake, and `gh` client parsing | `tests/test_github_seam.py` |
| workspace confinement, secret refusal, command allowlist | `tests/test_agent_workspace.py` |
| the fixer's state machine, its refuses, and its PR body | `tests/test_issue_fix_workflow.py` |
| the reviewer's gate, the auto-merge opt-in, diff truncation | `tests/test_pr_review_workflow.py` |
| the shared tool-loop and the workspace tool dispatch | `tests/test_agent_coding.py` |
| scenario loading + validation | `tests/test_eval_agent_scenarios.py` |
| the per-promise checks | `tests/test_eval_agent_checks.py` |
| the suite end to end | `tests/test_eval_agent_runner.py` |
| the evaluation-run history store | `tests/test_eval_store.py` |
| the CLI commands and their guards | `tests/test_agent_cli.py` |
| the `/api/admin/*` routes | `tests/test_admin_api.py` |

## Configuration

| Variable | Default | Used by |
|----------|---------|---------|
| `LOL_COACH_GITHUB_REPO` | — | `--repo`'s default, `owner/name` |
| `LOL_COACH_AGENT_ISSUE_LABEL` | `agent-fix` | `LoadIssue`'s opt-in gate |
| `LOL_COACH_AGENT_BASE_BRANCH` | `main` | the PR's base |
| `LOL_COACH_AGENT_BRANCH_PREFIX` | `agent/issue` | the agent's branch name |
| `LOL_COACH_AGENT_VERIFY_COMMANDS` | `ruff check .`, `mypy src`, `python -m pytest -q` | `Verify` |
| `LOL_COACH_AGENT_DENIED_PATHS` | `.github/workflows`, `.env` | `Implement`'s guard |
| `LOL_COACH_AGENT_LOOP_ROUNDS` | `12` | the coder's tool-loop budget |
| `LOL_COACH_AGENT_AUTO_MERGE` | `false` | whether an approval also merges |
