"""Tests for the pr-review workflow: the reviewer submits, a human merges.

The reviewer is read-only and never merges unless ``LOL_COACH_AGENT_AUTO_MERGE``
is explicitly on. The approve branch is gated on *both* the model's verdict and
the absence of a failing check, which the "approve with a red check" case pins.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from lolcoach.agent.reviewer import ReviewCheck, ReviewComment, ReviewVerdict, ScriptedReviewer
from lolcoach.agent.workspace import FakeWorkspace
from lolcoach.config import Settings, get_settings
from lolcoach.github.fake import FakeGitHubClient
from lolcoach.github.types import APPROVE, REQUEST_CHANGES, PullRequest
from lolcoach.workflow.executor import ExecutionResult, WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.workflows.pr_review import build_pr_review

_PR = PullRequest(number=7, title="fix the widget", head="agent/issue-12", base="main")


def _run(
    tmp_path: Path,
    *,
    verdict: ReviewVerdict,
    pr: PullRequest | None = None,
    diff: str = "--- a\n+++ b\n",
    settings: Settings | None = None,
    number: int = 7,
) -> tuple[ExecutionResult, FakeGitHubClient]:
    github = FakeGitHubClient()
    github.seed_pull_request(pr or _PR, diff=diff)
    built = build_pr_review(
        reviewer=ScriptedReviewer(verdict=verdict),
        github=github,
        workspace=FakeWorkspace(),
        settings=settings or get_settings(),
    )
    store = ExecutionStore.connect(tmp_path / "executions.db")
    try:
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        result = executor.run({"pull_request_number": number})
    finally:
        store.close()
    return result, github


def _approved(**overrides: object) -> ReviewVerdict:
    base: dict[str, object] = {
        "decision": "approve",
        "summary": "looks correct",
        "checks": (ReviewCheck("tests", ok=True),),
    }
    base.update(overrides)
    return ReviewVerdict(**base)  # type: ignore[arg-type]


def test_a_clean_verdict_approves_and_does_not_merge(tmp_path: Path) -> None:
    result, github = _run(tmp_path, verdict=_approved())

    assert result.status == "succeeded"
    assert [review.event for review in github.reviews] == [APPROVE]
    assert github.merged == []
    assert result.output.get("merged") is False


def test_a_request_changes_verdict_sends_it_back(tmp_path: Path) -> None:
    result, github = _run(
        tmp_path,
        verdict=ReviewVerdict(
            decision="request_changes",
            summary="needs work",
            comments=(ReviewComment(path="src/a.py", line=3, body="npe here"),),
        ),
    )

    assert result.status == "failed"
    assert [review.event for review in github.reviews] == [REQUEST_CHANGES]
    assert "requested changes" in (result.error or "")
    assert github.merged == []


def test_an_approve_with_a_failing_check_is_still_sent_back(tmp_path: Path) -> None:
    verdict = _approved(checks=(ReviewCheck("tests", ok=False, detail="2 failed"),))
    result, github = _run(tmp_path, verdict=verdict)

    assert result.status == "failed"
    assert [review.event for review in github.reviews] == [REQUEST_CHANGES]
    assert github.merged == []


def test_the_review_body_lists_checks_and_comments(tmp_path: Path) -> None:
    _, github = _run(
        tmp_path,
        verdict=_approved(
            comments=(ReviewComment(path="src/a.py", line=3, body="nit"),),
        ),
    )

    body = github.reviews[0].body
    assert "[x] tests" in body
    assert "`src/a.py:3` nit" in body
    assert "pr-review agent" in body


def test_auto_merge_is_off_by_default(tmp_path: Path) -> None:
    assert get_settings().agent_auto_merge is False


def test_auto_merge_opt_in_merges_an_approval(tmp_path: Path) -> None:
    settings = replace(get_settings(), agent_auto_merge=True)
    result, github = _run(tmp_path, verdict=_approved(), settings=settings)

    assert result.status == "succeeded"
    assert github.merged == [7]
    assert result.output.get("merged") is True


def test_auto_merge_does_not_merge_a_request_changes(tmp_path: Path) -> None:
    settings = replace(get_settings(), agent_auto_merge=True)
    result, github = _run(
        tmp_path, verdict=ReviewVerdict(decision="request_changes"), settings=settings
    )

    assert result.status == "failed"
    assert github.merged == []


def test_a_merged_pull_request_is_refused(tmp_path: Path) -> None:
    pr = PullRequest(number=7, title="t", head="x", base="main", state="merged")
    result, github = _run(tmp_path, verdict=_approved(), pr=pr)

    assert result.status == "failed"
    assert "not open" in (result.error or "")
    assert github.reviews == []


def test_a_missing_pull_request_number_is_refused(tmp_path: Path) -> None:
    github = FakeGitHubClient()
    built = build_pr_review(
        reviewer=ScriptedReviewer(),
        github=github,
        workspace=FakeWorkspace(),
        settings=get_settings(),
    )
    store = ExecutionStore.connect(tmp_path / "executions.db")
    try:
        result = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        ).run({"pull_request_number": 0})
    finally:
        store.close()

    assert result.status == "failed"
    assert "pull_request_number is required" in (result.error or "")


def test_an_oversized_diff_is_truncated_for_the_model(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, verdict=_approved(), diff="x" * 20001)

    assert result.output.get("diff_truncated") is True
    assert isinstance(result.output.get("diff"), str)
    assert len(str(result.output["diff"])) == 20000
