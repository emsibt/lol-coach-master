"""Tests for the deterministic tool surface the agent orchestrates.

The load-bearing invariant here: ``suggest_pick`` is the only source of a pick,
and every other tool returns evidence, never a decision.
"""

from __future__ import annotations

from typing import cast

import pytest

from lolcoach.agent.tools import ToolBox, champions_in_payloads, empty_board
from lolcoach.knowledge.ontology import Ontology, get_ontology


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast("list[object]", value)


@pytest.fixture(scope="module")
def ontology() -> Ontology:
    return get_ontology()


@pytest.fixture()
def box(ontology: Ontology) -> ToolBox:
    return ToolBox(ontology)


def test_set_board_resolves_aliases_casing_and_reports_unknowns(box: ToolBox) -> None:
    result = box.set_board(
        {
            "allies": ["Jinx", "mf"],
            "enemies": ["tf"],
            "ally_bans": ["Zed"],
            "enemy_bans": ["zzzznotachampion"],
        }
    )
    assert result.ok
    board = _dict(result.payload["board"])
    assert board["allies"] == ["jinx", "missfortune"]
    assert board["enemies"] == ["twistedfate"]
    assert board["ally_bans"] == ["zed"]
    assert result.payload["unresolved"] == ["zzzznotachampion"]


def test_set_board_ignores_unknown_roles(box: ToolBox) -> None:
    result = box.set_board({"allies": ["jinx"], "open_roles": ["mid", "bench"]})
    assert _dict(result.payload["board"])["open_roles"] == ["mid"]


def test_suggest_pick_runs_the_engine(box: ToolBox) -> None:
    result = box.suggest_pick(
        {
            "allies": ["jinx"],
            "enemies": ["zeri"],
            "ally_bans": [],
            "enemy_bans": [],
            "open_roles": [],
        }
    )
    assert result.ok
    assert result.payload["counter"] is not None or result.payload["synergy"] is not None
    assert result.payload["problems"] == []
    assert result.payload["traces"] is not None


def test_explain_matchup_returns_the_measured_edge(box: ToolBox, ontology: Ontology) -> None:
    relation = ontology.relations[0]
    result = box.explain_matchup(
        {"champion_a": relation.source, "champion_b": relation.target, "role": relation.position}
    )
    assert result.ok
    edges = _list(result.payload["edges"])
    assert any(_dict(edge)["reason"] == relation.reason for edge in edges)


def test_explain_matchup_rejects_an_unknown_champion(box: ToolBox) -> None:
    result = box.explain_matchup({"champion_a": "jinx", "champion_b": "zzzznotachampion"})
    assert not result.ok
    assert "error" in result.payload


def test_get_champion_returns_roles_and_meta(box: ToolBox) -> None:
    result = box.get_champion({"champion": "jinx"})
    assert result.ok
    assert result.payload["slug"] == "jinx"
    positions = _list(result.payload["positions"])
    assert positions
    first = _dict(positions[0])
    assert {"position", "win_rate", "pick_rate", "ban_rate"} <= set(first)


def test_get_champion_rejects_an_unknown_name(box: ToolBox) -> None:
    assert not box.get_champion({"champion": "zzzznotachampion"}).ok


def test_unknown_tool_name_is_a_failed_result(box: ToolBox) -> None:
    result = box.call("fabricate_pick", {}, empty_board())
    assert not result.ok


def test_champions_in_payloads_only_finds_known_slugs(ontology: Ontology) -> None:
    found = champions_in_payloads(
        ontology, [{"counter": {"slug": "jinx"}}, {"note": "zzzznotachampion"}]
    )
    assert found == {"jinx"}
