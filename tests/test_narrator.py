"""Tests for the grounded narrator.

The load-bearing invariant: the narrator may not surface any number that is not
already present in the engine record it was given.
"""

from __future__ import annotations

import json
import re

import pytest

from lolcoach.agent.agent import ToolStep
from lolcoach.agent.llm import LlmUnavailableError
from lolcoach.agent.narrator import (
    Narration,
    allowed_champions,
    narrate,
    narrate_final,
    offline_final_text,
    offline_text,
    unsourced_champions,
)
from lolcoach.config import get_settings
from lolcoach.knowledge.ontology import get_ontology

_EMPTY_BOARD: dict[str, list[str]] = {
    "allies": [],
    "enemies": [],
    "ally_bans": [],
    "enemy_bans": [],
    "open_roles": [],
}


def _record() -> dict[str, object]:
    return {
        "counter": {
            "slug": "qiyana",
            "name": "Qiyana",
            "role": "jungle",
            "score": 0.0217,
            "traces": [
                {
                    "kind": "counter",
                    "source": "counter:qiyana->vi:jungle",
                    "text": "qiyana counters vi in jungle: 53.0% over 2,665 games",
                    "delta": 0.0217,
                }
            ],
        },
        "synergy": {
            "slug": "twisted_fate",
            "name": "Twisted Fate",
            "role": "mid",
            "score": 0.0573,
            "traces": [
                {
                    "kind": "synergy",
                    "source": "synergy:jinx~twisted_fate:mid",
                    "text": "jinx + twisted_fate in mid: 56.0% over 1,910 games",
                    "delta": 0.0573,
                }
            ],
        },
    }


def test_offline_narration_names_the_picks_and_their_evidence() -> None:
    text = offline_text(_record())
    assert "Qiyana" in text and "jungle" in text
    assert "Twisted Fate" in text
    assert "53.0% over 2,665 games" in text


def test_offline_narration_asserts_no_unsourced_numbers() -> None:
    text = offline_text(_record())
    blob = json.dumps(_record())
    numbers = set(re.findall(r"\d[\d,]*(?:\.\d+)?%?", text))
    assert numbers, "expected the narration to cite at least one number"
    unsourced = sorted(token for token in numbers if token not in blob)
    assert unsourced == [], f"narrator surfaced numbers not in the record: {unsourced}"


def test_narrate_falls_back_to_offline_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        narration = narrate(_record())
    finally:
        get_settings.cache_clear()
    assert isinstance(narration, Narration)
    assert narration.mode == "offline"
    assert narration.text.strip()


def test_offline_handles_an_empty_record() -> None:
    text = offline_text({})
    assert "none available" in text.lower()


def _suggest_step() -> ToolStep:
    record = _record()
    return ToolStep(
        call_id="call-1",
        name="suggest_pick",
        arguments={},
        ok=True,
        payload={
            "counter": record["counter"],
            "synergy": record["synergy"],
            "traces": [],
            "problems": [],
            "board": _EMPTY_BOARD,
        },
    )


def test_offline_final_summarizes_the_suggest_result() -> None:
    text = offline_final_text([_suggest_step()], get_ontology())
    assert "Qiyana" in text
    assert "Twisted Fate" in text


def test_narrate_final_requires_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(LlmUnavailableError):
            narrate_final("", [], _EMPTY_BOARD, ontology=get_ontology())
    finally:
        get_settings.cache_clear()


def test_narrate_final_propagates_a_model_failure() -> None:
    class _BoomClient:
        provider = "boom"

        def complete(
            self,
            _messages: object,
            _tools: object,
            *,
            tool_choice: str | None = None,
        ) -> object:
            raise RuntimeError("upstream 500")

    with pytest.raises(RuntimeError, match="upstream 500"):
        narrate_final(
            "who to pick?",
            [_suggest_step()],
            _EMPTY_BOARD,
            client=_BoomClient(),
            ontology=get_ontology(),
        )


def test_unsourced_champions_ignores_allowed_sources() -> None:
    ontology = get_ontology()
    assert unsourced_champions("Yasuo is the pick.", {"jinx"}, ontology) == {"yasuo"}
    assert unsourced_champions("Jinx and Zeri.", {"jinx", "zeri"}, ontology) == set()


class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class _TextClient:
    provider = "fake"

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(
        self,
        _messages: object,
        _tools: object,
        *,
        tool_choice: str | None = None,
    ) -> _Reply:
        return _Reply(self._text)


def _tips_step(sources: tuple[str, ...]) -> ToolStep:
    """A tips payload whose prose names a champion that is *not* in the draft."""
    return ToolStep(
        call_id="call-tips",
        name="get_champion_tips",
        arguments={"champion": "galio"},
        ok=True,
        payload={
            "slug": "galio",
            "name": "Galio",
            "lore": "lore",
            "blurb": "blurb",
            "tags": ["Tank"],
            "ally_tips": ["Strong against yasuo"],
            "enemy_tips": ["Watch the roam"],
            "abilities": [{"slot": "P", "name": "Colossal Smash", "description": "passive"}],
        },
        sources=sources,
    )


def _build_step() -> ToolStep:
    return ToolStep(
        call_id="call-build",
        name="get_champion_build",
        arguments={"champion": "galio", "role": "mid"},
        ok=True,
        payload={
            "slug": "galio",
            "name": "Galio",
            "position": "mid",
            "skill_order": ["Q", "E"],
            "items": [{"item_type": "core", "names": ["Luden's"], "win_rate": 0.54,
                       "pick_rate": 0.3}],
            "runes": [{"page": "Sorcery", "picks": ["Summon Aery"]}],
        },
        sources=("galio",),
    )


def test_allowed_champions_does_not_widen_on_tip_prose() -> None:
    ontology = get_ontology()
    allowed = allowed_champions(ontology, [_tips_step(("galio",))], _EMPTY_BOARD)
    assert "galio" in allowed
    # "yasuo" appears in the payload, but only in prose — it is not sourced.
    assert "yasuo" not in allowed


def test_allowed_champions_still_walks_engine_payloads() -> None:
    """Non-prose tools keep the original whole-payload walk."""
    ontology = get_ontology()
    champion_step = ToolStep(
        call_id="call-2",
        name="get_champion",
        arguments={},
        ok=True,
        payload={"slug": "jinx", "name": "Jinx", "positions": []},
    )
    allowed = allowed_champions(ontology, [champion_step, _suggest_step()], _EMPTY_BOARD)
    assert {"jinx", "qiyana"} <= allowed


def test_allowed_champions_includes_the_board() -> None:
    ontology = get_ontology()
    board = {**_EMPTY_BOARD, "enemies": ["jinx"]}
    assert "jinx" in allowed_champions(ontology, [], board)


def test_narrate_final_drops_a_reply_that_names_unsourced_prose() -> None:
    """A tip mentions Yasuo; that must not license the narrator to name him."""
    narration = narrate_final(
        "how do I play galio?",
        [_tips_step(("galio",))],
        _EMPTY_BOARD,
        client=_TextClient("Galio struggles against Yasuo in the mid lane."),
        ontology=get_ontology(),
    )
    assert narration.mode == "offline"
    assert narration.text.startswith("Galio:")


def test_narrate_final_accepts_a_reply_grounded_in_its_sources() -> None:
    narration = narrate_final(
        "how do I play galio?",
        [_tips_step(("galio",))],
        _EMPTY_BOARD,
        client=_TextClient("Play safe as Galio and watch the roam."),
        ontology=get_ontology(),
    )
    assert narration.mode == "llm"


def test_offline_final_summarizes_tips() -> None:
    text = offline_final_text([_tips_step(("galio",))], get_ontology())
    assert "Galio:" in text
    assert "P Colossal Smash" in text
    assert "Playing as" in text and "Playing against" in text


def test_offline_final_summarizes_a_build() -> None:
    text = offline_final_text([_build_step()], get_ontology())
    assert "Galio (mid)" in text
    assert "Core: Luden's" in text
    assert "Runes - Sorcery: Summon Aery" in text
    assert "Skill order: Q > E" in text


def test_offline_final_prefers_the_pick_over_reference_blocks() -> None:
    """A pick is the answer; reference material only fills in when there is none."""
    text = offline_final_text([_tips_step(("galio",)), _suggest_step()], get_ontology())
    assert "Qiyana" in text
    assert "Playing as" not in text


def test_offline_final_reports_missing_reference_data() -> None:
    step = ToolStep(
        call_id="call-1",
        name="get_champion_tips",
        arguments={},
        ok=True,
        payload={"slug": "galio", "name": "Galio", "available": False},
        sources=("galio",),
    )
    assert "no tips are recorded" in offline_final_text([step], get_ontology())


def _guide_step() -> ToolStep:
    return ToolStep(
        call_id="call-guide",
        name="get_lane_matchup_guide",
        arguments={},
        ok=True,
        payload={
            "champion": "Galio",
            "opponent": "Ahri",
            "position": "mid",
            "tip": "Engage Ahri whenever she steps in range.",
            "solo_kill_advantage": "Ahri",
            "lane_advantage": "Ahri",
            "play_style": "even",
            "champion_win_rate": 0.488625,
            "games": 56131,
        },
        sources=("galio", "ahri"),
    )


def _profile_step() -> ToolStep:
    return ToolStep(
        call_id="call-profile",
        name="get_summoner_profile",
        arguments={},
        ok=True,
        payload={
            "riot_id": "Hide on bush#KR1",
            "region": "KR",
            "ladder": {"rank": 12, "total": 5000},
            "queues": [
                {"queue": "SOLORANKED", "tier": "CHALLENGER", "division": 1, "lp": 2184,
                 "wins": 444, "losses": 370, "hot_streak": True}
            ],
            "season": {"queue": "RANKED", "season_id": 33, "games": 814, "wins": 445},
        },
        sources=(),
    )


def test_offline_final_summarizes_a_matchup_guide() -> None:
    text = offline_final_text([_guide_step()], get_ontology())
    assert "Galio vs Ahri (mid)" in text
    assert "48.9%" in text and "56,131 games" in text
    assert "Engage Ahri whenever she steps in range." in text


def test_offline_final_summarizes_a_profile() -> None:
    text = offline_final_text([_profile_step()], get_ontology())
    assert "Hide on bush#KR1 (KR)" in text
    assert "Challenger 1, 2184 LP (444-370)" in text
    assert "Solo/Duo" in text


def test_allowed_champions_does_not_widen_on_a_guide_tip() -> None:
    ontology = get_ontology()
    allowed = allowed_champions(ontology, [_guide_step()], _EMPTY_BOARD)
    assert {"galio", "ahri"} <= allowed


def test_a_profile_licenses_no_champion() -> None:
    """A player's profile is not a draft fact, so it sources nothing at all."""
    ontology = get_ontology()
    allowed = allowed_champions(ontology, [_profile_step()], _EMPTY_BOARD)
    assert allowed == set()
