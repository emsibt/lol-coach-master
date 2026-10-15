"""Tests for the evaluation scenario loader and its validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach.eval.scenarios import ScenarioError, default_scenarios_path, load_scenarios


def test_default_suite_loads_and_ids_are_unique() -> None:
    scenarios = load_scenarios()
    ids = [scenario.id for scenario in scenarios]
    assert len(ids) == len(set(ids))
    assert "fuzzy-pick-request" in ids
    assert default_scenarios_path().exists()


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError):
        load_scenarios(tmp_path / "nope.yaml")


def test_scenario_requires_a_message(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("scenarios:\n  - id: a\n", encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_scenarios(path)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n  - {id: a, message: hi}\n  - {id: a, message: yo}\n", encoding="utf-8"
    )
    with pytest.raises(ScenarioError):
        load_scenarios(path)


def test_unknown_recommendation_mode_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n  - id: a\n    message: hi\n    expect: {recommendation: maybe}\n",
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError):
        load_scenarios(path)


def test_expectations_parse_into_typed_fields(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "scenarios:\n"
        "  - id: a\n"
        "    name: A case\n"
        "    message: jinx vs zeri\n"
        "    board: {allies: [jinx]}\n"
        "    expect:\n"
        "      tools_called: [set_board, suggest_pick]\n"
        "      tools_not_called: [explain_matchup]\n"
        "      max_tool_calls: 3\n"
        "      board: {enemies: [zeri]}\n"
        "      require_no_unresolved: true\n"
        "      recommendation: required\n"
        "      mode: llm\n"
        "      reply_mentions: [jinx]\n",
        encoding="utf-8",
    )
    (scenario,) = load_scenarios(path)
    assert scenario.id == "a"
    assert scenario.name == "A case"
    assert scenario.board == {"allies": ["jinx"]}
    expect = scenario.expect
    assert expect.tools_called == ("set_board", "suggest_pick")
    assert expect.tools_not_called == ("explain_matchup",)
    assert expect.max_tool_calls == 3
    assert expect.board == {"enemies": ("zeri",)}
    assert expect.require_no_unresolved is True
    assert expect.recommendation == "required"
    assert expect.mode == "llm"
    assert expect.reply_mentions == ("jinx",)
    # Invariants default on.
    assert expect.grounded is True
    assert expect.pick_from_engine is True
