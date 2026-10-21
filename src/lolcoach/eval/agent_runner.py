"""Run the maintenance-agent scenarios through the real workflows and score them.

Each scenario is executed by the *production* issue-fix/pr-review workflow with
every seam replaced by its fake — the GitHub client, the workspace, the coder or
reviewer, and the publisher. So what is evaluated is exactly what ships: the same
state machine, the same guardrails in the same task bodies, the same PR body and
review submission. The only substitutions are the transport and the brain.

Because nothing touches the network, the token, or the disk, the suite is
hermetic and runs in CI on every change. Each scenario's execution is persisted,
so any failure opens in the Trace Inspector.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lolcoach.agent.fixer import ScriptedCoder
from lolcoach.agent.publisher import RecordingPublisher
from lolcoach.agent.reviewer import ScriptedReviewer
from lolcoach.agent.workspace import CommandResult, FakeWorkspace
from lolcoach.config import Settings, get_settings
from lolcoach.eval.agent_checks import RunEffects, evaluate_fix, evaluate_review
from lolcoach.eval.agent_scenarios import (
    AgentScenario,
    FixScenario,
    ReviewScenario,
    kind_of,
)
from lolcoach.eval.checks import CheckOutcome
from lolcoach.github.fake import FakeGitHubClient
from lolcoach.observability import tracing
from lolcoach.workflow.executor import ExecutionResult, WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.workflows.issue_fix import build_issue_fix
from lolcoach.workflow.workflows.pr_review import build_pr_review

logger = logging.getLogger(__name__)

# The suite's fixed contract: the verification commands and guarded paths the
# shipped agent promises. Pinned here (not read from settings) so the eval tests
# that guarantee, not whatever the local environment happens to configure.
DEFAULT_VERIFY_COMMANDS = ("ruff check .", "mypy src", "python -m pytest -q")
DEFAULT_DENIED_PATHS = (".github/workflows", ".env")


@dataclass(frozen=True, slots=True)
class AgentScenarioResult:
    scenario_id: str
    name: str
    kind: str
    execution_id: str
    status: str
    error: str | None
    checks: tuple[CheckOutcome, ...]

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[CheckOutcome, ...]:
        return tuple(check for check in self.checks if not check.ok)


@dataclass(frozen=True, slots=True)
class AgentSuiteReport:
    provider: str
    model: str
    results: tuple[AgentScenarioResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "agent",
            "provider": self.provider,
            "model": self.model,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "scenarios": [
                {
                    "id": result.scenario_id,
                    "name": result.name,
                    "kind": result.kind,
                    "passed": result.passed,
                    "execution_id": result.execution_id,
                    "status": result.status,
                    "error": result.error,
                    "checks": [
                        {"name": check.name, "ok": check.ok, "detail": check.detail}
                        for check in result.checks
                    ],
                }
                for result in self.results
            ],
        }


def run_agent_suite(
    scenarios: Sequence[AgentScenario],
    *,
    store_path: Path,
    settings: Settings | None = None,
) -> AgentSuiteReport:
    """Execute every agent scenario once against the fakes and score it."""
    active = settings or get_settings()
    store = ExecutionStore.connect(store_path)
    results: list[AgentScenarioResult] = []
    try:
        for scenario in scenarios:
            results.append(_run_scenario(scenario, active, store))
    finally:
        store.close()
    return AgentSuiteReport(
        provider=active.llm_provider, model=active.llm_model, results=tuple(results)
    )


def _run_scenario(
    scenario: AgentScenario, settings: Settings, store: ExecutionStore
) -> AgentScenarioResult:
    if isinstance(scenario, FixScenario):
        result, effects = _run_fix(scenario, settings, store)
    else:
        result, effects = _run_review(scenario, settings, store)
    checks = (
        evaluate_fix(scenario, result, effects)
        if isinstance(scenario, FixScenario)
        else evaluate_review(scenario, result, effects)
    )
    logger.info(
        "Agent scenario evaluated",
        extra={
            "data": {
                "scenario": scenario.id,
                "passed": all(check.ok for check in checks),
                "execution_id": result.execution_id,
            }
        },
    )
    return AgentScenarioResult(
        scenario_id=scenario.id,
        name=scenario.name,
        kind=kind_of(scenario),
        execution_id=result.execution_id,
        status=result.status,
        error=result.error,
        checks=checks,
    )


def _run_fix(
    scenario: FixScenario, settings: Settings, store: ExecutionStore
) -> tuple[ExecutionResult, RunEffects]:
    github = FakeGitHubClient()
    github.seed_issue(scenario.issue)
    workspace = FakeWorkspace()
    workspace.seed(
        scenario.repo,
        [(command, CommandResult(command, code)) for command, code in scenario.verify.items()],
    )
    publisher = RecordingPublisher()
    coder = ScriptedCoder(
        investigation=scenario.investigation,
        plan_result=scenario.plan,
        implementation_summary=scenario.summary,
        writes=scenario.writes,
        notes=scenario.notes,
    )
    built = build_issue_fix(
        coder=coder,
        github=github,
        workspace=workspace,
        publisher=publisher,
        settings=settings,
        verify_commands=DEFAULT_VERIFY_COMMANDS,
        denied_paths=DEFAULT_DENIED_PATHS,
    )
    executor = WorkflowExecutor(
        built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
    )
    result = _execute(executor, scenario, {"issue_number": scenario.issue.number}, settings)
    effects = RunEffects(github=github, publisher=publisher, workspace=workspace)
    return result, effects


def _run_review(
    scenario: ReviewScenario, settings: Settings, store: ExecutionStore
) -> tuple[ExecutionResult, RunEffects]:
    github = FakeGitHubClient()
    github.seed_pull_request(scenario.pull_request, diff=scenario.diff)
    workspace = FakeWorkspace()
    workspace.seed(scenario.repo)
    built = build_pr_review(
        reviewer=ScriptedReviewer(verdict=scenario.review),
        github=github,
        workspace=workspace,
        settings=settings,
    )
    executor = WorkflowExecutor(
        built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
    )
    result = _execute(
        executor, scenario, {"pull_request_number": scenario.pull_request.number}, settings
    )
    effects = RunEffects(github=github, publisher=RecordingPublisher(), workspace=workspace)
    return result, effects


def _execute(
    executor: WorkflowExecutor,
    scenario: AgentScenario,
    initial: dict[str, object],
    settings: Settings,
) -> ExecutionResult:
    with tracing.trace(
        settings,
        name=f"eval:agent:{scenario.id}",
        scope="agent",
        tags=("eval", "agent", scenario.id),
        metadata={"scenario": scenario.id, "kind": kind_of(scenario)},
    ):
        return executor.run(initial)


def format_agent_summary(report: AgentSuiteReport) -> str:
    """A compact per-scenario verdict list plus a pass/fail tally."""
    lines = ["Maintenance-agent evaluation", ""]
    for result in report.results:
        verdict = "PASS" if result.passed else "FAIL"
        lines.append(f"[{verdict}] {result.scenario_id} -- {result.name}")
        lines.append(f"       kind: {result.kind}  status: {result.status}")
        for failure in result.failures:
            lines.append(f"       - {failure.name}: {failure.detail}")
    lines.append("")
    lines.append(f"{report.passed}/{report.total} scenarios passed")
    if not report.ok:
        lines.append("failing executions are in the eval DB and open in the Trace Inspector")
    return "\n".join(lines)
