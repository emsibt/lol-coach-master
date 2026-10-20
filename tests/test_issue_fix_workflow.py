"""Tests for the issue-fix workflow: the fixer's guardrails and its PR body.

The workflow runs against the fakes (a scripted coder, an in-memory GitHub, an
in-memory workspace, a recording publisher), so what is exercised is the shipped
state machine and the checks in its task bodies — not a mock of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lolcoach.agent.fixer import FixPlan, Investigation, ScriptedCoder
from lolcoach.agent.publisher import RecordingPublisher
from lolcoach.agent.workspace import CommandResult, FakeWorkspace
from lolcoach.config import get_settings
from lolcoach.github.fake import FakeGitHubClient
from lolcoach.github.types import Issue, PullRequest
from lolcoach.workflow.executor import ExecutionResult, WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.workflows.issue_fix import build_issue_fix

VERIFY = ("ruff check .", "mypy src")
DENIED = (".github/workflows", ".env")


@dataclass
class _Harness:
    result: ExecutionResult
    github: FakeGitHubClient
    workspace: FakeWorkspace
    publisher: RecordingPublisher


def _run(
    tmp_path: Path,
    *,
    coder: ScriptedCoder,
    verify: dict[str, int] | None = None,
    issue: Issue | None = None,
    seed_pr: PullRequest | None = None,
) -> _Harness:
    github = FakeGitHubClient()
    if issue is not None:
        github.seed_issue(issue)
    if seed_pr is not None:
        github.seed_pull_request(seed_pr)
    workspace = FakeWorkspace()
    workspace.seed(
        {"src/app.py": "print('app')\n"},
        [(command, CommandResult(command, code)) for command, code in (verify or {}).items()],
    )
    publisher = RecordingPublisher()
    built = build_issue_fix(
        coder=coder,
        github=github,
        workspace=workspace,
        publisher=publisher,
        settings=get_settings(),
        base_branch="main",
        branch_prefix="agent/issue",
        issue_label="agent-fix",
        verify_commands=VERIFY,
        denied_paths=DENIED,
    )
    store = ExecutionStore.connect(tmp_path / "executions.db")
    try:
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        result = executor.run({"issue_number": (issue or _ISSUE).number})
    finally:
        store.close()
    return _Harness(result=result, github=github, workspace=workspace, publisher=publisher)


_ISSUE = Issue(
    number=12,
    title="the widget crashes",
    body="steps to reproduce",
    labels=("agent-fix",),
)


def _good_coder(
    writes: tuple[tuple[str, str], ...] = (("src/app.py", "fixed\n"),),
) -> ScriptedCoder:
    return ScriptedCoder(
        investigation=Investigation(
            summary="the widget dereferences a null",
            findings=("src/app.py:3 dereferences null",),
            files=("src/app.py",),
            confidence=0.9,
            is_real_issue=True,
        ),
        plan_result=FixPlan(
            summary="guard the dereference",
            steps=("add a guard", "run the tests"),
            files=("src/app.py",),
            test_plan="pytest tests",
        ),
        implementation_summary="guarded the dereference",
        writes=writes,
    )


def test_a_clean_run_opens_a_linked_pull_request(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        coder=_good_coder(),
        issue=_ISSUE,
        verify={"ruff check .": 0, "mypy src": 0},
    )

    assert run.result.status == "succeeded"
    assert run.result.error is None
    assert len(run.github.created) == 1
    pr = run.github.created[0]
    assert pr.head == "agent/issue-12"
    assert pr.base == "main"
    assert "Closes #12" in pr.body
    assert "## Investigation" in pr.body
    assert "## Verification" in pr.body
    assert run.publisher.publishes[0]["branch"] == "agent/issue-12"
    assert run.workspace.changed_files() == ["src/app.py"]


def test_an_unlabelled_issue_is_refused_untouched(tmp_path: Path) -> None:
    issue = Issue(number=12, title="sneaky", labels=("bug",))
    run = _run(tmp_path, coder=_good_coder(), issue=issue, verify={"ruff check .": 0})

    assert run.result.status == "failed"
    assert "not labelled" in (run.result.error or "")
    assert run.github.created == []
    assert run.publisher.publishes == []
    assert run.workspace.writes == []


def test_an_existing_pull_request_stops_the_run(tmp_path: Path) -> None:
    existing = PullRequest(number=99, title="t", head="agent/issue-12", base="main")
    run = _run(tmp_path, coder=_good_coder(), issue=_ISSUE, seed_pr=existing)

    assert run.result.status == "failed"
    assert "already targets" in (run.result.error or "")
    assert run.github.created == []


def test_a_non_actionable_investigation_blocks_before_planning(tmp_path: Path) -> None:
    coder = ScriptedCoder(
        investigation=Investigation(is_real_issue=False, confidence=0.0)
    )
    run = _run(tmp_path, coder=coder, issue=_ISSUE)

    assert run.result.status == "failed"
    assert run.workspace.writes == []
    assert "Blocked" in _states(run)


def test_a_low_confidence_investigation_is_not_actionable(tmp_path: Path) -> None:
    coder = ScriptedCoder(
        investigation=Investigation(is_real_issue=True, confidence=0.4)
    )
    run = _run(tmp_path, coder=coder, issue=_ISSUE)

    assert run.result.status == "failed"
    assert run.workspace.writes == []


def test_a_guarded_path_aborts_the_run(tmp_path: Path) -> None:
    coder = _good_coder(writes=((".github/workflows/ci.yml", "on: push\n"),))
    run = _run(tmp_path, coder=coder, issue=_ISSUE)

    assert run.result.status == "failed"
    assert "guarded paths" in (run.result.error or "")
    assert run.github.created == []
    assert run.publisher.publishes == []


def test_a_failing_verification_blocks_the_pull_request(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        coder=_good_coder(),
        issue=_ISSUE,
        verify={"ruff check .": 0, "mypy src": 1},
    )

    assert run.result.status == "failed"
    assert run.github.created == []
    assert run.publisher.publishes == []
    verification = run.result.output.get("verification")
    assert isinstance(verification, dict)
    assert verification.get("passed") is False


def test_a_change_with_no_files_is_refused(tmp_path: Path) -> None:
    run = _run(tmp_path, coder=_good_coder(writes=()), issue=_ISSUE)

    assert run.result.status == "failed"
    assert "no file changes" in (run.result.error or "")
    assert run.github.created == []


def test_a_missing_issue_number_is_refused(tmp_path: Path) -> None:
    github = FakeGitHubClient()
    built = build_issue_fix(
        coder=_good_coder(),
        github=github,
        workspace=FakeWorkspace(),
        publisher=RecordingPublisher(),
        settings=get_settings(),
        verify_commands=VERIFY,
    )
    store = ExecutionStore.connect(tmp_path / "executions.db")
    try:
        result = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        ).run({})
    finally:
        store.close()

    assert result.status == "failed"
    assert "issue_number is required" in (result.error or "")


def _states(run: _Harness) -> Sequence[str]:
    out: list[str] = []
    for event in run.result.events:
        name = event.state_name
        if name and name not in out:
            out.append(name)
    return out
