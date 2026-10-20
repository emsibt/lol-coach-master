"""Tests for the maintenance-agent checks: one pure function per promise.

Each check reads only what a run observably did, so a synthetic execution plus a
hand-built effects bundle is enough to pin every check's pass and fail branch.
"""

from __future__ import annotations

from lolcoach.agent.fixer import FixPlan, Investigation
from lolcoach.agent.publisher import RecordingPublisher
from lolcoach.agent.reviewer import ReviewVerdict
from lolcoach.agent.workspace import FakeWorkspace
from lolcoach.eval.agent_checks import RunEffects, evaluate_fix, evaluate_review, visited_states
from lolcoach.eval.agent_scenarios import (
    FixExpectations,
    FixScenario,
    ReviewExpectations,
    ReviewScenario,
)
from lolcoach.eval.checks import CheckOutcome
from lolcoach.github.fake import FakeGitHubClient
from lolcoach.github.types import APPROVE, Issue, PullRequest, Review
from lolcoach.workflow.events import (
    STATE_ENTERED,
    STATE_FAILED,
    STATE_SUCCEEDED,
)
from lolcoach.workflow.events import Event as _Event
from lolcoach.workflow.executor import ExecutionResult


def _event(event_type: str, state: str | None) -> _Event:
    return _Event(
        execution_id="x", seq=0, type=event_type, state_name=state, timestamp="2026-01-01T00:00:00Z"
    )


def _result(
    status: str = "succeeded",
    *,
    error: str | None = None,
    output: dict[str, object] | None = None,
    states: tuple[tuple[str, str], ...] = (),
) -> ExecutionResult:
    events = tuple(_event(event_type, state) for event_type, state in states)
    return ExecutionResult(
        execution_id="x", status=status, output=output or {}, error=error, events=events
    )


def _effects(
    *,
    created: list[PullRequest] | None = None,
    publish: bool = False,
    writes: tuple[tuple[str, str], ...] = (),
    reviews: list[Review] | None = None,
    merged: list[int] | None = None,
) -> RunEffects:
    github = FakeGitHubClient()
    for pr in created or []:
        github.created.append(pr)
    github.reviews.extend(reviews or [])
    github.merged.extend(merged or [])
    publisher = RecordingPublisher()
    if publish:
        publisher.publish(branch="agent/issue-1", base="main", message="m")
    workspace = FakeWorkspace()
    for path, content in writes:
        workspace.write(path, content)
    return RunEffects(github=github, publisher=publisher, workspace=workspace)


def _fix_scenario(expect: FixExpectations) -> FixScenario:
    return FixScenario(
        id="s",
        name="s",
        issue=Issue(number=1, title="t", labels=("agent-fix",)),
        repo={},
        investigation=Investigation(),
        plan=FixPlan(),
        writes=(),
        summary="",
        notes=(),
        verify={},
        expect=expect,
    )


def _pr(number: int, head: str = "agent/issue-1", body: str = "Closes #1") -> PullRequest:
    return PullRequest(number=number, title="t", body=body, head=head, base="main")


def _by_name(checks: tuple[CheckOutcome, ...]) -> dict[str, CheckOutcome]:
    return {check.name: check for check in checks}


def test_visited_states_appends_the_terminal_state() -> None:
    result = _result(
        states=(
            (STATE_ENTERED, "A"),
            (STATE_ENTERED, "B"),
            (STATE_SUCCEEDED, "Done"),
        )
    )
    assert visited_states(result) == ("A", "B", "Done")


def test_visited_states_does_not_duplicate_a_failed_task_state() -> None:
    result = _result(
        status="failed",
        states=((STATE_ENTERED, "A"), (STATE_FAILED, "A")),
    )
    assert visited_states(result) == ("A",)


def test_a_clean_fix_run_passes_every_check() -> None:
    scenario = _fix_scenario(
        FixExpectations(
            branch="agent/issue-1",
            files_changed=("src/a.py",),
            verification_passed=True,
        )
    )
    result = _result(
        output={"verification": {"passed": True}},
        states=((STATE_ENTERED, "LoadIssue"), (STATE_SUCCEEDED, "Succeeded")),
    )
    effects = _effects(created=[_pr(5)], publish=True, writes=(("src/a.py", "x"),))

    checks = _by_name(evaluate_fix(scenario, result, effects))

    assert all(check.ok for check in checks.values()), checks
    assert checks["branch"].ok
    assert checks["pr_links_issue"].ok


def test_a_run_that_should_not_open_a_pr_fails_when_it_does() -> None:
    scenario = _fix_scenario(FixExpectations(pr_opened=False, published=False, no_writes=True))
    result = _result(status="failed", states=((STATE_FAILED, "Blocked"),))
    effects = _effects(created=[_pr(5)], publish=True, writes=(("src/a.py", "x"),))

    checks = _by_name(evaluate_fix(scenario, result, effects))

    assert checks["pr_opened"].ok is False
    assert checks["published"].ok is False
    assert checks["no_writes"].ok is False


def test_a_wrong_branch_is_caught() -> None:
    scenario = _fix_scenario(FixExpectations(branch="agent/issue-99"))
    effects = _effects(created=[_pr(5, head="agent/issue-1")], publish=True)

    checks = _by_name(evaluate_fix(scenario, _result(), effects))

    assert checks["branch"].ok is False


def test_a_pull_request_that_does_not_link_the_issue_is_caught() -> None:
    scenario = _fix_scenario(FixExpectations())
    effects = _effects(created=[_pr(5, body="no link here")], publish=True)

    checks = _by_name(evaluate_fix(scenario, _result(), effects))

    assert checks["pr_links_issue"].ok is False


def test_a_verification_mismatch_is_caught() -> None:
    scenario = _fix_scenario(FixExpectations(verification_passed=True))
    result = _result(output={"verification": {"passed": False}})

    checks = _by_name(evaluate_fix(scenario, result, _effects()))

    assert checks["verification_passed"].ok is False


def test_error_contains_is_only_checked_when_asked() -> None:
    without = _by_name(evaluate_fix(_fix_scenario(FixExpectations()), _result(), _effects()))
    assert "error_contains" not in without

    wanted = _by_name(
        evaluate_fix(
            _fix_scenario(FixExpectations(status="failed", error_contains="guarded")),
            _result(status="failed", error="touched guarded paths"),
            _effects(),
        )
    )
    assert wanted["error_contains"].ok is True


def test_states_must_appear_in_order() -> None:
    scenario = _fix_scenario(FixExpectations(states=("LoadIssue", "Verify", "Succeeded")))
    result = _result(
        states=(
            (STATE_ENTERED, "Verify"),
            (STATE_ENTERED, "LoadIssue"),
            (STATE_SUCCEEDED, "Succeeded"),
        )
    )
    checks = _by_name(evaluate_fix(scenario, result, _effects()))
    assert checks["states"].ok is False


def _review_scenario(expect: ReviewExpectations) -> ReviewScenario:
    return ReviewScenario(
        id="r",
        name="r",
        pull_request=PullRequest(number=1, title="t"),
        diff="",
        repo={},
        review=ReviewVerdict(),
        expect=expect,
    )


def test_a_clean_approval_passes() -> None:
    scenario = _review_scenario(ReviewExpectations(review_event=APPROVE))
    effects = _effects(reviews=[Review(number=1, event=APPROVE, body="ok")])
    result = _result(states=((STATE_SUCCEEDED, "Succeeded"),))

    checks = _by_name(evaluate_review(scenario, result, effects))

    assert all(check.ok for check in checks.values())
    assert checks["merged"].ok  # not merged, and none expected


def test_a_reviewer_that_merges_fails_the_merged_check() -> None:
    scenario = _review_scenario(ReviewExpectations(review_event=APPROVE, merged=False))
    effects = _effects(reviews=[Review(number=1, event=APPROVE, body="ok")], merged=[1])

    checks = _by_name(evaluate_review(scenario, _result(), effects))

    assert checks["merged"].ok is False


def test_the_wrong_review_event_is_caught() -> None:
    scenario = _review_scenario(ReviewExpectations(review_event=APPROVE))
    effects = _effects(reviews=[Review(number=1, event="REQUEST_CHANGES", body="no")])

    checks = _by_name(evaluate_review(scenario, _result(status="failed"), effects))

    assert checks["review_event"].ok is False


def test_no_review_is_expected_when_the_event_is_blank() -> None:
    scenario = _review_scenario(ReviewExpectations())
    checks = _by_name(evaluate_review(scenario, _result(), _effects()))
    assert checks["review_event"].ok is True
