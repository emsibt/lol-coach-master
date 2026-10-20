"""Tests for the maintenance-agent evaluation runner (hermetic end to end).

The shipped workflows run against the fakes, so the checked-in suite is the test:
if a guardrail regresses, a scenario goes red here.
"""

from __future__ import annotations

from pathlib import Path

from lolcoach.config import get_settings
from lolcoach.eval.agent_runner import (
    AgentSuiteReport,
    format_agent_summary,
    run_agent_suite,
)
from lolcoach.eval.agent_scenarios import FixScenario, ReviewScenario, load_agent_scenarios
from lolcoach.workflow.store import ExecutionStore


def test_the_checked_in_suite_all_passes(tmp_path: Path) -> None:
    report = run_agent_suite(load_agent_scenarios(), store_path=tmp_path / "eval.db")

    assert isinstance(report, AgentSuiteReport)
    assert report.total == 7
    assert report.failed == 0, [
        (r.scenario_id, [f"{c.name}: {c.detail}" for c in r.failures]) for r in report.results
    ]
    assert report.ok is True


def test_every_scenario_persists_its_execution(tmp_path: Path) -> None:
    store_path = tmp_path / "eval.db"
    report = run_agent_suite(load_agent_scenarios(), store_path=store_path)

    store = ExecutionStore.connect(store_path)
    try:
        for result in report.results:
            assert result.execution_id
            assert store.get_execution(result.execution_id) is not None
    finally:
        store.close()


def test_the_report_serializes_for_a_ci_artifact(tmp_path: Path) -> None:
    report = run_agent_suite(load_agent_scenarios(), store_path=tmp_path / "eval.db")
    payload = report.to_dict()

    assert payload["kind"] == "agent"
    assert payload["total"] == 7
    assert payload["passed"] == 7
    assert payload["provider"] == get_settings().llm_provider
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    first = scenarios[0]
    assert isinstance(first, dict)
    assert "checks" in first


def test_the_summary_lists_every_scenario(tmp_path: Path) -> None:
    report = run_agent_suite(load_agent_scenarios(), store_path=tmp_path / "eval.db")
    summary = format_agent_summary(report)

    assert "[PASS] fix-real-bug" in summary
    assert "[PASS] review-approves-a-clean-change" in summary
    assert "7/7 scenarios passed" in summary


def test_a_subset_can_be_run(tmp_path: Path) -> None:
    scenarios = [s for s in load_agent_scenarios() if isinstance(s, ReviewScenario)]
    report = run_agent_suite(scenarios, store_path=tmp_path / "eval.db")

    assert report.total == len(scenarios)
    assert all(r.kind == "pr_review" for r in report.results)


def test_fix_and_review_kinds_are_reported(tmp_path: Path) -> None:
    scenario = next(s for s in load_agent_scenarios() if isinstance(s, FixScenario))
    report = run_agent_suite([scenario], store_path=tmp_path / "eval.db")
    assert report.results[0].kind == "issue_fix"
