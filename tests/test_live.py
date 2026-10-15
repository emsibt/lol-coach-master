"""Tests for the live provider (caching, throttling, and payload shaping).

No network: the provider takes a client factory, so a fake client stands in for
the MCP session. The behaviour worth pinning down is the *defensiveness* — the
cache, the min-interval gate, and turning a failure into ``LiveDataError`` — plus
the fact that only rank/record (never a champion) reaches the model.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from lolcoach.agent.tools import LiveDataError
from lolcoach.config import get_settings
from lolcoach.dataprovider import live as live_module
from lolcoach.dataprovider.live import OpggLiveProvider, build_live_provider
from lolcoach.dataprovider.opgg_mcp import OpggMcpError

_PROFILE = {
    "data": {
        "summoner": {
            "ladder_rank": {"rank": 12, "total": 5000},
            "league_stats": [
                {
                    "game_type": "SOLORANKED",
                    "win": 444,
                    "lose": 370,
                    "play": 814,
                    "is_hot_streak": True,
                    "is_veteran": False,
                    "tier_info": {"tier": "CHALLENGER", "division": 1, "lp": 2184, "level": None},
                },
                {
                    # An unranked queue: nothing to report, so it is dropped.
                    "game_type": "FLEXRANKED",
                    "win": None,
                    "lose": None,
                    "tier_info": {"tier": None, "division": None, "lp": None, "level": None},
                },
            ],
            "most_champions": {"game_type": "RANKED", "season_id": 33, "play": 814, "win": 445},
        }
    }
}

_GUIDE = {
    "lang": "en_US",
    "position": "mid",
    "my_champion": "Galio",
    "opponent_champion": "Ahri",
    "data": {
        "summary": {
            "positions": [
                {"name": "MID", "stats": {"play": 56131, "win_rate": 0.488625}},
                {"name": "TOP", "stats": {"play": 1000, "win_rate": 0.5}},
            ]
        },
        "opponent_champion_tip": "Engage Ahri whenever she steps in range.",
        "lane_solo_kill_advantage_champion": "Ahri",
        "lane_advantage_champion": "Ahri",
        "recommended_play_style": "even",
    },
}


class _FakeClient:
    def __init__(self, responses: dict[str, object], *, error: Exception | None = None) -> None:
        self._responses = responses
        self._error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        if self._error is not None:
            raise self._error
        return self._responses.get(name, {})


def _provider(
    responses: dict[str, object],
    *,
    error: Exception | None = None,
    **overrides: Any,
) -> tuple[OpggLiveProvider, _FakeClient]:
    defaults: dict[str, Any] = {"live_min_interval": 0.0}
    settings = replace(get_settings(), **{**defaults, **overrides})
    client = _FakeClient(responses, error=error)
    def factory() -> _FakeClient:
        return client

    return OpggLiveProvider(settings, client_factory=factory), client


def test_profile_reports_rank_and_record_only() -> None:
    provider, client = _provider({"lol_get_summoner_profile": _PROFILE})
    payload = provider.summoner_profile("Hide on bush#KR1", "KR")

    assert payload["riot_id"] == "Hide on bush#KR1"
    assert payload["region"] == "KR"
    assert payload["ladder"] == {"rank": 12, "total": 5000}
    queues = payload["queues"]
    assert isinstance(queues, list) and len(queues) == 1  # the unranked queue is dropped
    assert queues[0] == {
        "queue": "SOLORANKED",
        "tier": "CHALLENGER",
        "division": 1,
        "lp": 2184,
        "wins": 444,
        "losses": 370,
        "hot_streak": True,
    }
    assert payload["season"] == {
        "queue": "RANKED",
        "season_id": 33,
        "games": 814,
        "wins": 445,
        "losses": 0,
    }
    # The payload must carry no champion name: the guard would have to license it.
    assert "champion" not in str(payload).lower()
    assert client.calls[0][1]["game_name"] == "Hide on bush"
    assert client.calls[0][1]["tag_line"] == "KR1"


def test_profile_uses_the_default_region() -> None:
    provider, client = _provider({"lol_get_summoner_profile": _PROFILE})
    provider.summoner_profile("Name#TAG")
    assert client.calls[0][1]["region"] == get_settings().live_region


def test_profile_rejects_a_riot_id_without_a_tag() -> None:
    provider, _client = _provider({"lol_get_summoner_profile": _PROFILE})
    with pytest.raises(LiveDataError, match="Riot ID"):
        provider.summoner_profile("justaname")


def test_a_failed_lookup_raises_live_data_error() -> None:
    provider, _client = _provider({}, error=OpggMcpError("upstream 503"))
    with pytest.raises(LiveDataError, match="upstream 503"):
        provider.summoner_profile("Name#TAG")


def test_matchup_guide_shapes_the_tip_and_the_lane_stats() -> None:
    provider, client = _provider({"lol_get_lane_matchup_guide": _GUIDE})
    payload = provider.lane_matchup("GALIO", "AHRI", "mid")

    assert payload["champion"] == "Galio"
    assert payload["opponent"] == "Ahri"
    assert payload["position"] == "mid"
    assert payload["tip"] == "Engage Ahri whenever she steps in range."
    assert payload["lane_advantage"] == "Ahri"
    assert payload["solo_kill_advantage"] == "Ahri"
    assert payload["play_style"] == "even"
    # The MID row, not the champion's all-lane average.
    assert payload["champion_win_rate"] == 0.488625
    assert payload["games"] == 56131
    assert client.calls[0][1] == {
        "my_champion": "GALIO",
        "opponent_champion": "AHRI",
        "position": "mid",
    }


def test_a_repeated_lookup_is_served_from_the_cache() -> None:
    provider, client = _provider({"lol_get_summoner_profile": _PROFILE})
    first = provider.summoner_profile("Name#TAG")
    second = provider.summoner_profile("Name#TAG")

    assert first == second
    assert len(client.calls) == 1  # the second call never left the process


def test_the_cache_expires() -> None:
    provider, client = _provider({"lol_get_summoner_profile": _PROFILE}, live_cache_ttl=0.0)
    provider.summoner_profile("Name#TAG")
    provider.summoner_profile("Name#TAG")
    assert len(client.calls) == 2


def test_different_arguments_are_cached_separately() -> None:
    provider, client = _provider({"lol_get_summoner_profile": _PROFILE})
    provider.summoner_profile("Name#TAG")
    provider.summoner_profile("Other#TAG")
    assert len(client.calls) == 2


def test_the_min_interval_gate_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(live_module.time, "sleep", slept.append)
    provider, _client = _provider({"lol_get_summoner_profile": _PROFILE}, live_min_interval=0.25)

    provider.summoner_profile("Name#TAG")  # first call: nothing to wait for
    provider.summoner_profile("Other#TAG")  # second: throttled
    assert slept and slept[0] <= 0.25


def test_build_live_provider_is_a_singleton() -> None:
    build_live_provider.cache_clear()
    try:
        assert build_live_provider() is build_live_provider()
    finally:
        build_live_provider.cache_clear()
