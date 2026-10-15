"""Run the golden scenarios through the real agent tool-loop and score them.

Each scenario is executed by the *production* agent workflow
(:func:`build_agent_chat`) with the injected model client and reference data — so
what is evaluated is exactly what ships, not a reimplementation. The only things
the runner adds are a fresh model client per scenario (so a scripted client stays
stateless across the suite) and a persisted execution, so any failing run can be
opened in the Trace Inspector.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lolcoach.agent.llm import LlmClient
from lolcoach.agent.tools import LiveDataProvider, normalize_board
from lolcoach.config import Settings
from lolcoach.eval.checks import CheckOutcome, evaluate
from lolcoach.eval.scenarios import Scenario
from lolcoach.knowledge.ontology import Ontology
from lolcoach.knowledge.reference import ReferenceData
from lolcoach.values import as_str, opt_dict, opt_list
from lolcoach.workflow.executor import WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.workflows.agent_chat import build_agent_chat

logger = logging.getLogger(__name__)


class EvalError(RuntimeError):
    """The suite cannot run at all (e.g. no model is configured)."""


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario_id: str
    name: str
    execution_id: str
    status: str
    mode: str
    reply: str
    tool_names: tuple[str, ...]
    board: dict[str, list[str]]
    error: str | None
    checks: tuple[CheckOutcome, ...]

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[CheckOutcome, ...]:
        return tuple(check for check in self.checks if not check.ok)


@dataclass(frozen=True, slots=True)
class SuiteReport:
    provider: str
    model: str
    results: tuple[ScenarioResult, ...]

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


def run_suite(
    scenarios: Sequence[Scenario],
    *,
    ontology: Ontology,
    client_factory: Callable[[], LlmClient | None],
    store_path: Path,
    settings: Settings | None = None,
    reference: ReferenceData | None = None,
    live: LiveDataProvider | None = None,
) -> SuiteReport:
    """Execute every scenario once and return the scored report.

    ``client_factory`` is called once per scenario so each run gets a fresh model
    client; a scripted client therefore starts clean for every scenario. It must
    not return ``None`` — a missing model means the agent cannot think, so the
    suite fails fast rather than scoring empty runs.
    """
    store = ExecutionStore.connect(store_path)
    results: list[ScenarioResult] = []
    try:
        for scenario in scenarios:
            client = client_factory()
            if client is None:
                raise EvalError(
                    "no LLM is configured: set LOLCOACH_LLM_PROVIDER and its API key"
                )
            results.append(
                _run_scenario(scenario, ontology, client, store, settings, reference, live)
            )
    finally:
        store.close()

    return SuiteReport(
        provider=settings.llm_provider if settings is not None else "unknown",
        model=settings.llm_model if settings is not None else "unknown",
        results=tuple(results),
    )


def _run_scenario(
    scenario: Scenario,
    ontology: Ontology,
    client: LlmClient,
    store: ExecutionStore,
    settings: Settings | None,
    reference: ReferenceData | None,
    live: LiveDataProvider | None,
) -> ScenarioResult:
    built = build_agent_chat(
        ontology, client=client, settings=settings, reference=reference, live=live
    )
    executor = WorkflowExecutor(
        built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
    )
    initial: dict[str, object] = {"question": scenario.message, "board": scenario.board}
    if scenario.image is not None:
        initial["image"] = scenario.image
    result = executor.run(initial)

    checks = evaluate(scenario, result, ontology)
    output = result.output
    tool_names = tuple(
        as_str(opt_dict(item).get("name")) for item in opt_list(output.get("tool_calls"))
    )
    logger.info(
        "Scenario evaluated",
        extra={
            "data": {
                "scenario": scenario.id,
                "passed": all(check.ok for check in checks),
                "execution_id": result.execution_id,
            }
        },
    )
    return ScenarioResult(
        scenario_id=scenario.id,
        name=scenario.name,
        execution_id=result.execution_id,
        status=result.status,
        mode=as_str(output.get("mode")),
        reply=as_str(output.get("reply")),
        tool_names=tool_names,
        board=normalize_board(output.get("board")),
        error=result.error,
        checks=checks,
    )
