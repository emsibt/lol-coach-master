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

        def complete(self, _messages: object, _tools: object) -> object:
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
