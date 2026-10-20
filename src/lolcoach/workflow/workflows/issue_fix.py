"""The issue-fixer agent, expressed as a workflow definition.

    LoadIssue -> Investigate -> Assess(Choice) --not real--> Blocked(Fail)
                                    | real issue
                                    v
                       Plan -> Implement -> Verify -> Verified(Choice)
                                                          | green
                                                          v
                                          OpenPullRequest -> Succeeded
                                                          | red
                                                          v
                                                       Blocked(Fail)

Running it as our own state machine is the point: every phase's structured
result — the investigation, the plan, the changed files, the verification — is a
persisted state output, so the Trace Inspector (and the Agent Runs view) renders
exactly what the agent found, planned, and did, not just that it finished.

Deterministic guardrails live in the task bodies, not the prompt: the issue must
carry the opt-in label, an open PR for the branch fails the run, and a change to
a guarded path (CI, secrets) aborts it. The model proposes; the workflow checks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from lolcoach.agent.fixer import Coder, FixPlan, Implementation, Investigation
from lolcoach.agent.publisher import RepoPublisher
from lolcoach.agent.workspace import Workspace
from lolcoach.config import Settings, get_settings
from lolcoach.github.client import GitHubClient
from lolcoach.github.types import Issue
from lolcoach.values import as_bool, as_int, as_str, opt_dict, opt_list
from lolcoach.workflow.types import (
    ChoiceRule,
    ChoiceState,
    FailState,
    State,
    SucceedState,
    TaskState,
    Workflow,
)

logger = logging.getLogger(__name__)

TaskFn = Callable[[dict[str, object]], dict[str, object]]
PredicateFn = Callable[[dict[str, object]], bool]

_MIN_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class IssueFix:
    workflow: Workflow
    tasks: dict[str, TaskFn]
    predicates: dict[str, PredicateFn]


def _is_denied(path: str, denied: Sequence[str]) -> bool:
    return any(path == entry or path.startswith(entry) for entry in denied)


def _pr_body(
    issue: Issue,
    investigation: Investigation,
    plan: FixPlan,
    implementation: Implementation,
    verification: dict[str, object],
) -> str:
    commands = [opt_dict(item) for item in opt_list(verification.get("commands"))]
    lines = [
        f"Closes #{issue.number}",
        "",
        "## Investigation",
        investigation.summary or "(none)",
        *[f"- {finding}" for finding in investigation.findings],
        "",
        "## Plan",
        *[f"{index + 1}. {step}" for index, step in enumerate(plan.steps)],
        f"\nTest plan: {plan.test_plan}" if plan.test_plan else "",
        "",
        "## Change",
        implementation.summary or "(none)",
        *[f"- `{path}`" for path in implementation.files_changed],
        "",
        "## Verification",
        *[
            f"- `{as_str(entry.get('command'))}` → exit {as_int(entry.get('exit_code'))}"
            for entry in commands
        ],
        "",
        "_Opened by the lol-coach issue-fix agent. A human reviews and merges._",
    ]
    return "\n".join(line for line in lines if line is not None)


def build_issue_fix(
    *,
    coder: Coder,
    github: GitHubClient,
    workspace: Workspace,
    publisher: RepoPublisher,
    settings: Settings | None = None,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    issue_label: str | None = None,
    verify_commands: Sequence[str] | None = None,
    denied_paths: Sequence[str] | None = None,
) -> IssueFix:
    """Build the issue-fix workflow plus its task/predicate registries."""
    active = settings or get_settings()
    base = base_branch or active.agent_base_branch
    prefix = branch_prefix or active.agent_branch_prefix
    label = issue_label or active.agent_issue_label
    commands = (
        tuple(verify_commands) if verify_commands is not None else active.agent_verify_commands
    )
    denied = tuple(denied_paths) if denied_paths is not None else active.agent_denied_paths

    def load_issue(data: dict[str, object]) -> dict[str, object]:
        number = as_int(data.get("issue_number"))
        if number <= 0:
            raise ValueError("issue_number is required")
        issue = github.get_issue(number)
        if label not in issue.labels:
            raise ValueError(
                f"issue #{number} is not labelled {label!r}; refusing to act on it"
            )
        branch = f"{prefix}-{number}"
        existing = github.find_pull_request(head=branch)
        if existing is not None:
            raise ValueError(
                f"pull request #{existing.number} already targets {branch}; nothing to do"
            )
        logger.info(
            "Issue loaded", extra={"data": {"issue": number, "branch": branch, "label": label}}
        )
        return {"issue": issue.to_dict(), "branch": branch, "base_branch": base}

    def investigate(data: dict[str, object]) -> dict[str, object]:
        issue = Issue.from_dict(data.get("issue"))
        result = coder.investigate(issue, workspace)
        logger.info(
            "Issue investigated",
            extra={
                "data": {
                    "issue": issue.number,
                    "real": result.is_real_issue,
                    "confidence": result.confidence,
                }
            },
        )
        return {"investigation": result.to_dict()}

    def plan(data: dict[str, object]) -> dict[str, object]:
        issue = Issue.from_dict(data.get("issue"))
        investigation = Investigation.from_dict(data.get("investigation"))
        result = coder.plan(issue, investigation, workspace)
        return {"plan": result.to_dict()}

    def implement(data: dict[str, object]) -> dict[str, object]:
        issue = Issue.from_dict(data.get("issue"))
        plan_result = FixPlan.from_dict(data.get("plan"))
        result = coder.implement(issue, plan_result, workspace)
        offenders = [path for path in result.files_changed if _is_denied(path, denied)]
        if offenders:
            raise ValueError(f"the change touched guarded paths: {offenders}")
        if not result.files_changed:
            raise ValueError("the agent produced no file changes")
        logger.info(
            "Change implemented",
            extra={"data": {"issue": issue.number, "files": len(result.files_changed)}},
        )
        return {"implementation": result.to_dict()}

    def verify(data: dict[str, object]) -> dict[str, object]:
        results = [workspace.run(command).to_dict() for command in commands]
        passed = all(as_int(opt_dict(entry).get("exit_code")) == 0 for entry in results)
        logger.info("Change verified", extra={"data": {"passed": passed, "commands": len(results)}})
        return {"verification": {"passed": passed, "commands": results}}

    def open_pull_request(data: dict[str, object]) -> dict[str, object]:
        issue = Issue.from_dict(data.get("issue"))
        investigation = Investigation.from_dict(data.get("investigation"))
        plan_result = FixPlan.from_dict(data.get("plan"))
        implementation = Implementation.from_dict(data.get("implementation"))
        verification = opt_dict(data.get("verification"))
        branch = as_str(data.get("branch"))
        message = f"fix: {issue.title}\n\nCloses #{issue.number}"
        publisher.publish(branch=branch, base=base, message=message)
        pr = github.create_pull_request(
            title=f"fix: {issue.title}",
            body=_pr_body(issue, investigation, plan_result, implementation, verification),
            head=branch,
            base=base,
        )
        logger.info(
            "Pull request opened", extra={"data": {"issue": issue.number, "pr": pr.number}}
        )
        return {
            "pull_request": pr.to_dict(),
            "summary": implementation.summary or issue.title,
        }

    def issue_actionable(data: dict[str, object]) -> bool:
        investigation = Investigation.from_dict(data.get("investigation"))
        return investigation.is_real_issue and investigation.confidence >= _MIN_CONFIDENCE

    def verification_passed(data: dict[str, object]) -> bool:
        return as_bool(opt_dict(data.get("verification")).get("passed"))

    states: dict[str, State] = {
        "LoadIssue": TaskState("LoadIssue", "load_issue", next="Investigate"),
        "Investigate": TaskState("Investigate", "investigate", next="Assess"),
        "Assess": ChoiceState(
            "Assess",
            (ChoiceRule("issue_actionable", "Plan"),),
            default="Blocked",
        ),
        "Plan": TaskState("Plan", "plan", next="Implement"),
        "Implement": TaskState("Implement", "implement", next="Verify"),
        "Verify": TaskState("Verify", "verify", next="Verified"),
        "Verified": ChoiceState(
            "Verified",
            (ChoiceRule("verification_passed", "OpenPullRequest"),),
            default="Blocked",
        ),
        "OpenPullRequest": TaskState("OpenPullRequest", "open_pull_request", next="Succeeded"),
        "Succeeded": SucceedState("Succeeded"),
        "Blocked": FailState(
            "Blocked",
            error="the agent stopped: the issue was not actionable or the change did not verify",
        ),
    }

    workflow = Workflow(name="issue-fix", version="1", start_at="LoadIssue", states=states)
    tasks: dict[str, TaskFn] = {
        "load_issue": load_issue,
        "investigate": investigate,
        "plan": plan,
        "implement": implement,
        "verify": verify,
        "open_pull_request": open_pull_request,
    }
    predicates: dict[str, PredicateFn] = {
        "issue_actionable": issue_actionable,
        "verification_passed": verification_passed,
    }
    return IssueFix(workflow=workflow, tasks=tasks, predicates=predicates)
