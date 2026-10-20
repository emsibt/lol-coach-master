"""The PR-reviewer agent, expressed as a workflow definition.

    LoadPullRequest -> Review -> Gate(Choice) --approved--> Approve -> Succeeded
                                     | changes requested
                                     v
                              RequestChanges -> ChangesRequested(Fail)

The reviewer only reads: the workflow submits an ``APPROVE`` or a
``REQUEST_CHANGES`` review and stops. It never merges — a human does — so the
agent can never land its own (or the fixer's) code. ``LOL_COACH_AGENT_AUTO_MERGE``
exists as an explicit opt-in, off by default, and is the only path to a merge.

The approve branch is gated on *both* the model's verdict and the absence of any
failing check it reported, so a verdict of "approve" alongside a failing check
still requests changes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from lolcoach.agent.reviewer import Reviewer, ReviewVerdict
from lolcoach.agent.workspace import Workspace
from lolcoach.config import Settings, get_settings
from lolcoach.github.client import GitHubClient
from lolcoach.github.types import APPROVE as GH_APPROVE
from lolcoach.github.types import REQUEST_CHANGES as GH_REQUEST_CHANGES
from lolcoach.github.types import PullRequest
from lolcoach.values import as_int, as_str
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

_MAX_DIFF_CHARS = 20000


@dataclass(frozen=True, slots=True)
class PrReview:
    workflow: Workflow
    tasks: dict[str, TaskFn]
    predicates: dict[str, PredicateFn]


def _review_body(verdict: ReviewVerdict) -> str:
    lines = [verdict.summary or "(no summary)", ""]
    if verdict.checks:
        lines.append("Checks:")
        lines.extend(
            f"- {'[x]' if check.ok else '[ ]'} {check.name}: {check.detail}"
            for check in verdict.checks
        )
        lines.append("")
    if verdict.comments:
        lines.append("Comments:")
        lines.extend(
            f"- `{comment.path}:{comment.line}` {comment.body}"
            if comment.path
            else f"- {comment.body}"
            for comment in verdict.comments
        )
    lines.append("")
    lines.append("_Reviewed by the lol-coach pr-review agent._")
    return "\n".join(lines)


def build_pr_review(
    *,
    reviewer: Reviewer,
    github: GitHubClient,
    workspace: Workspace,
    settings: Settings | None = None,
) -> PrReview:
    """Build the pr-review workflow plus its task/predicate registries."""
    active = settings or get_settings()
    auto_merge = active.agent_auto_merge

    def load_pull_request(data: dict[str, object]) -> dict[str, object]:
        number = as_int(data.get("pull_request_number"))
        if number <= 0:
            raise ValueError("pull_request_number is required")
        pr = github.get_pull_request(number)
        if pr.state != "open":
            raise ValueError(f"pull request #{number} is {pr.state}, not open")
        diff = github.pull_request_diff(number)
        logger.info(
            "Pull request loaded",
            extra={"data": {"pr": number, "diff_chars": len(diff)}},
        )
        return {
            "pull_request": pr.to_dict(),
            "diff": diff[:_MAX_DIFF_CHARS],
            "diff_truncated": len(diff) > _MAX_DIFF_CHARS,
        }

    def review(data: dict[str, object]) -> dict[str, object]:
        pr = PullRequest.from_dict(data.get("pull_request"))
        diff = as_str(data.get("diff"))
        verdict = reviewer.review(pr, diff, workspace)
        logger.info(
            "Pull request reviewed",
            extra={
                "data": {
                    "pr": pr.number,
                    "decision": verdict.decision,
                    "blocking": len(verdict.blocking),
                }
            },
        )
        return {"review": verdict.to_dict()}

    def _submit(data: dict[str, object], event: str) -> dict[str, object]:
        pr = PullRequest.from_dict(data.get("pull_request"))
        verdict = ReviewVerdict.from_dict(data.get("review"))
        review = github.submit_review(pr.number, event=event, body=_review_body(verdict))
        merged = False
        if event == GH_APPROVE and auto_merge:
            merged = github.merge_pull_request(pr.number)
            logger.warning("Auto-merged pull request", extra={"data": {"pr": pr.number}})
        return {
            "review_result": review.to_dict(),
            "merged": merged,
            "summary": verdict.summary,
        }

    def approve(data: dict[str, object]) -> dict[str, object]:
        return _submit(data, GH_APPROVE)

    def request_changes(data: dict[str, object]) -> dict[str, object]:
        return _submit(data, GH_REQUEST_CHANGES)

    def review_approved(data: dict[str, object]) -> bool:
        verdict = ReviewVerdict.from_dict(data.get("review"))
        return verdict.approved and not verdict.blocking

    states: dict[str, State] = {
        "LoadPullRequest": TaskState("LoadPullRequest", "load_pull_request", next="Review"),
        "Review": TaskState("Review", "review", next="Gate"),
        "Gate": ChoiceState(
            "Gate",
            (ChoiceRule("review_approved", "Approve"),),
            default="RequestChanges",
        ),
        "Approve": TaskState("Approve", "approve", next="Succeeded"),
        "RequestChanges": TaskState("RequestChanges", "request_changes", next="ChangesRequested"),
        "Succeeded": SucceedState("Succeeded"),
        "ChangesRequested": FailState(
            "ChangesRequested", error="the reviewer requested changes on the pull request"
        ),
    }

    workflow = Workflow(name="pr-review", version="1", start_at="LoadPullRequest", states=states)
    tasks: dict[str, TaskFn] = {
        "load_pull_request": load_pull_request,
        "review": review,
        "approve": approve,
        "request_changes": request_changes,
    }
    predicates: dict[str, PredicateFn] = {"review_approved": review_approved}
    return PrReview(workflow=workflow, tasks=tasks, predicates=predicates)
