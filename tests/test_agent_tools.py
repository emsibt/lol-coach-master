"""Tests for the deterministic tool surface the agent orchestrates.

The load-bearing invariant here: ``suggest_pick`` is the only source of a pick,
and every other tool returns evidence, never a decision.
"""

from __future__ import annotations

from typing import cast

import pytest

from lolcoach.agent.tools import (
    LiveDataError,
    ToolBox,
    champions_in_payloads,
    empty_board,
    tool_specs,
)
from lolcoach.knowledge.ontology import Ontology, get_ontology
from lolcoach.knowledge.reference import (
    Ability,
    ChampionBuild,
    ChampionReference,
    Item,
    ItemSet,
    ReferenceData,
    RunePage,
)


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


@pytest.fixture()
def reference() -> ReferenceData:
    return ReferenceData(
        champions={
            "galio": ChampionReference(
                slug="galio",
                name="Galio",
                lore="lore",
                blurb="blurb",
                tags=("Tank",),
                ally_tips=("Play safe",),
                enemy_tips=("Punish cooldowns",),
                abilities=(Ability("P", "Colossal Smash", "passive"),),
            )
        },
        builds={
            ("galio", "mid"): ChampionBuild(
                slug="galio",
                position="mid",
                skill_order=("Q", "E"),
                item_sets=(ItemSet("core", ("Luden's",), (6655,), 0.54, 0.3),),
                runes=(RunePage("Sorcery", ("Summon Aery",)),),
            )
        },
        items={6655: Item(6655, "Luden's", "dmg", 3000, (6653,), (1056,))},
    )


@pytest.fixture()
def ref_box(ontology: Ontology, reference: ReferenceData) -> ToolBox:
    return ToolBox(ontology, reference=reference)


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


def _tool_names(specs: list[dict[str, object]]) -> set[str]:
    return {str(_dict(_dict(spec)["function"])["name"]) for spec in specs}


def test_reference_tools_are_off_without_reference_data(box: ToolBox) -> None:
    assert box.reference_tools is False
    names = _tool_names(tool_specs())
    assert "get_champion_tips" not in names
    assert "get_champion_build" not in names


def test_reference_tools_are_advertised_when_loaded(ref_box: ToolBox) -> None:
    assert ref_box.reference_tools is True
    names = _tool_names(tool_specs(include_reference=True))
    assert {"get_champion_tips", "get_champion_build"} <= names


def test_get_champion_tips_sources_only_its_own_champion(ref_box: ToolBox) -> None:
    result = ref_box.get_champion_tips({"champion": "Galio"})
    assert result.ok
    assert result.payload["slug"] == "galio"
    assert result.payload["name"] == "Galio"
    assert result.payload["ally_tips"] == ["Play safe"]
    assert _list(result.payload["abilities"])[0] == {
        "slot": "P",
        "name": "Colossal Smash",
        "description": "passive",
    }
    assert result.sources == ("galio",)


def test_get_champion_build_returns_items_runes_and_skill_order(ref_box: ToolBox) -> None:
    result = ref_box.get_champion_build({"champion": "galio", "role": "mid"})
    assert result.ok
    assert result.payload["position"] == "mid"
    assert result.payload["skill_order"] == ["Q", "E"]
    items = _list(result.payload["items"])
    assert _dict(items[0])["item_type"] == "core"
    assert _dict(items[0])["names"] == ["Luden's"]
    assert _list(result.payload["runes"])[0] == {
        "page": "Sorcery",
        "picks": ["Summon Aery"],
    }
    assert result.sources == ("galio",)


def test_get_champion_build_ignores_an_unrecognized_role(ref_box: ToolBox) -> None:
    """A role we don't model must not hide the build the champion does have."""
    result = ref_box.get_champion_build({"champion": "galio", "role": "bench"})
    assert result.ok
    assert result.payload["position"] == "mid"


def test_get_champion_build_reports_a_lane_without_a_build(ref_box: ToolBox) -> None:
    result = ref_box.get_champion_build({"champion": "galio", "role": "jungle"})
    assert result.ok
    assert result.payload["available"] is False
    assert result.payload["role"] == "jungle"
    assert result.sources == ("galio",)


def test_reference_tools_report_missing_data_without_failing(ref_box: ToolBox) -> None:
    tips = ref_box.get_champion_tips({"champion": "jinx"})
    assert tips.ok
    assert tips.payload["available"] is False
    assert tips.sources == ("jinx",)
    build = ref_box.get_champion_build({"champion": "jinx"})
    assert build.ok
    assert build.payload["available"] is False


def test_reference_tools_reject_an_unknown_champion(ref_box: ToolBox) -> None:
    assert not ref_box.get_champion_tips({"champion": "zzzznotachampion"}).ok
    assert not ref_box.get_champion_build({"champion": "zzzznotachampion"}).ok


def test_call_dispatches_the_reference_tools(ref_box: ToolBox) -> None:
    result = ref_box.call("get_champion_build", {"champion": "galio"}, empty_board())
    assert result.ok
    assert result.sources == ("galio",)


def test_engine_tools_do_not_declare_sources(box: ToolBox) -> None:
    """Only prose tools source champions explicitly; the rest are payload-walked."""
    assert box.get_champion({"champion": "jinx"}).sources == ()
    assert box.explain_matchup({"champion_a": "jinx", "champion_b": "galio"}).sources == ()
    assert box.suggest_pick(empty_board()).sources == ()


class _FakeLive:
    """A LiveDataProvider stand-in that records how the toolbox called it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def summoner_profile(self, riot_id: str, region: str = "") -> dict[str, object]:
        self.calls.append(("profile", riot_id, region))
        return {"riot_id": riot_id, "region": region or "NA", "queues": [], "season": {}}

    def lane_matchup(self, champion: str, opponent: str, role: str) -> dict[str, object]:
        self.calls.append(("matchup", champion, opponent, role))
        return {"champion": champion, "opponent": opponent, "position": role, "tip": "a tip"}


@pytest.fixture()
def live_box(ontology: Ontology) -> tuple[ToolBox, _FakeLive]:
    live = _FakeLive()
    return ToolBox(ontology, live=live), live


def test_live_tools_are_off_without_a_provider(box: ToolBox) -> None:
    assert box.live_tools is False
    assert "get_summoner_profile" not in _tool_names(tool_specs())
    assert "get_lane_matchup_guide" not in _tool_names(tool_specs())
    assert not box.get_summoner_profile({"riot_id": "Name#TAG"}).ok
    assert not box.get_lane_matchup_guide(
        {"champion": "galio", "opponent": "ahri", "role": "mid"}
    ).ok


def test_live_tools_are_advertised_when_available(live_box: tuple[ToolBox, _FakeLive]) -> None:
    box, _live = live_box
    assert box.live_tools is True
    assert {"get_summoner_profile", "get_lane_matchup_guide"} <= _tool_names(
        tool_specs(include_live=True)
    )


def test_lane_matchup_guide_sources_both_champions(
    live_box: tuple[ToolBox, _FakeLive],
) -> None:
    box, live = live_box
    result = box.get_lane_matchup_guide({"champion": "Galio", "opponent": "Ahri", "role": "mid"})

    assert result.ok
    assert result.sources == ("galio", "ahri")
    # OP.GG wants UPPER_SNAKE identifiers, so the toolbox converts the slugs.
    assert live.calls == [("matchup", "GALIO", "AHRI", "mid")]


def test_lane_matchup_guide_needs_two_champions_and_a_lane(
    live_box: tuple[ToolBox, _FakeLive],
) -> None:
    box, _live = live_box
    assert not box.get_lane_matchup_guide(
        {"champion": "galio", "opponent": "zzzznotachampion", "role": "mid"}
    ).ok
    assert not box.get_lane_matchup_guide({"champion": "galio", "opponent": "ahri"}).ok
    assert not box.get_lane_matchup_guide(
        {"champion": "galio", "opponent": "ahri", "role": "bench"}
    ).ok


def test_summoner_profile_sources_nothing(live_box: tuple[ToolBox, _FakeLive]) -> None:
    box, live = live_box
    result = box.get_summoner_profile({"riot_id": "Hide on bush#KR1", "region": "kr"})

    assert result.ok
    assert result.sources == ()
    assert live.calls == [("profile", "Hide on bush#KR1", "kr")]


def test_summoner_profile_requires_a_riot_id(live_box: tuple[ToolBox, _FakeLive]) -> None:
    box, _live = live_box
    assert not box.get_summoner_profile({}).ok


def test_a_failing_live_lookup_is_a_failed_step(ontology: Ontology) -> None:
    class _Boom:
        def summoner_profile(self, riot_id: str, region: str = "") -> dict[str, object]:
            raise LiveDataError("upstream 503")

        def lane_matchup(self, champion: str, opponent: str, role: str) -> dict[str, object]:
            raise LiveDataError("upstream 503")

    box = ToolBox(ontology, live=_Boom())
    with pytest.raises(LiveDataError):
        box.get_summoner_profile({"riot_id": "Name#TAG"})
