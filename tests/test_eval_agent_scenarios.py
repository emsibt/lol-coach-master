"""Tests for the maintenance-agent scenario loader and its validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach.eval.agent_scenarios import (
    FIX,
    REVIEW,
    AgentScenarioError,
    FixScenario,
    ReviewScenario,
    default_agent_scenarios_path,
    kind_of,
    load_agent_scenarios,
)


def test_the_checked_in_suite_loads_with_unique_ids() -> None:
    scenarios = load_agent_scenarios()
    ids = [scenario.id for scenario in scenarios]

    assert len(scenarios) == 7
    assert len(ids) == len(set(ids))
    assert "fix-real-bug" in ids
    assert "review-approves-a-clean-change" in ids
    assert default_agent_scenarios_path().exists()


def test_the_suite_covers_both_kinds() -> None:
    kinds = {kind_of(scenario) for scenario in load_agent_scenarios()}
    assert kinds == {FIX, REVIEW}


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentScenarioError, match="not found"):
        load_agent_scenarios(tmp_path / "nope.yaml")


def test_an_empty_suite_raises(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("scenarios: []\n", encoding="utf-8")
    with pytest.raises(AgentScenarioError, match="no scenarios"):
        load_agent_scenarios(path)


def test_an_unknown_kind_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("scenarios:\n  - {id: a, kind: nope}\n", encoding="utf-8")
    with pytest.raises(AgentScenarioError, match="kind"):
        load_agent_scenarios(path)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n"
        "  - {id: a, kind: issue_fix, issue: {number: 1, title: t}}\n"
        "  - {id: a, kind: issue_fix, issue: {number: 2, title: t}}\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentScenarioError, match="duplicate"):
        load_agent_scenarios(path)


def test_a_fix_issue_needs_a_number_and_title(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n  - {id: a, kind: issue_fix, issue: {title: t}}\n", encoding="utf-8"
    )
    with pytest.raises(AgentScenarioError, match="positive 'number'"):
        load_agent_scenarios(path)


def test_an_invalid_expectation_status_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n"
        "  - id: a\n"
        "    kind: issue_fix\n"
        "    issue: {number: 1, title: t}\n"
        "    expect: {status: maybe}\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentScenarioError, match="succeeded"):
        load_agent_scenarios(path)


def test_an_invalid_review_event_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n"
        "  - id: a\n"
        "    kind: pr_review\n"
        "    pull_request: {number: 1, title: t}\n"
        "    expect: {review_event: MAYBE}\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentScenarioError, match="review_event"):
        load_agent_scenarios(path)


def test_a_fix_scenario_parses_into_typed_fields(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n"
        "  - id: a\n"
        "    name: A case\n"
        "    kind: issue_fix\n"
        "    issue: {number: 4, title: 't', labels: [agent-fix]}\n"
        "    investigation: {summary: s, confidence: 0.8, is_real_issue: true}\n"
        "    plan: {summary: p, steps: [one, two], test_plan: pytest}\n"
        "    implementation:\n"
        "      summary: done\n"
        "      writes: {src/a.py: 'new'}\n"
        "      notes: [n1]\n"
        "    verify: {'ruff check .': 0}\n"
        "    expect:\n"
        "      status: succeeded\n"
        "      states: [LoadIssue, Implement, Succeeded]\n"
        "      branch: agent/issue-4\n"
        "      files_changed: [src/a.py]\n"
        "      pr_opened: true\n",
        encoding="utf-8",
    )
    (scenario,) = load_agent_scenarios(path)
    assert isinstance(scenario, FixScenario)
    assert scenario.issue.number == 4
    assert scenario.issue.labels == ("agent-fix",)
    assert scenario.investigation.confidence == 0.8
    assert scenario.plan.steps == ("one", "two")
    assert scenario.writes == (("src/a.py", "new"),)
    assert scenario.verify == {"ruff check .": 0}
    expect = scenario.expect
    assert expect.branch == "agent/issue-4"
    assert expect.files_changed == ("src/a.py",)
    # Opening a PR implies a branch was published unless said otherwise.
    assert expect.published is True


def test_a_review_scenario_parses_into_typed_fields(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n"
        "  - id: r\n"
        "    kind: pr_review\n"
        "    pull_request: {number: 7, title: t, head: agent/issue-1}\n"
        "    diff: '--- a'\n"
        "    review:\n"
        "      decision: request_changes\n"
        "      summary: no\n"
        "      checks: [{name: tests, ok: false, detail: red}]\n"
        "    expect: {status: failed, review_event: request_changes, merged: false}\n",
        encoding="utf-8",
    )
    (scenario,) = load_agent_scenarios(path)
    assert isinstance(scenario, ReviewScenario)
    assert scenario.pull_request.number == 7
    assert scenario.review.decision == "request_changes"
    assert scenario.review.blocking[0].name == "tests"
    assert scenario.expect.review_event == "REQUEST_CHANGES"
