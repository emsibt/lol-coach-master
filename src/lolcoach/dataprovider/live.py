"""Live, per-user and per-matchup lookups served during a chat run.

Everything else the coach says is materialized offline: the knowledge graph and
the reference data are checked-in YAML, so a chat run never needs the network.
Two things cannot be checked in — a *player's* rank, and the guide for one
specific lane matchup — so they are fetched on demand, by the agent, only when a
question actually asks for them.

This module is the only place in ``dataprovider`` that answers a question during
a run, and it is deliberately defensive about it:

* a small retry budget and a short timeout, because a chat turn is waiting;
* a monotonic min-interval gate and a lock, because FastAPI runs these sync
  endpoints in a threadpool and OP.GG is a public server we do not want to hammer;
* a TTL cache keyed by the exact call, because a follow-up question ("and my
  duo?") tends to re-ask the same thing within seconds.

``agent/tools.py`` declares only the :class:`LiveDataProvider` Protocol; the
composition root injects :class:`OpggLiveProvider`. ``agent/**`` never imports
this module, so the network layer stays on this side of the seam.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Protocol

from lolcoach.agent.tools import LiveDataError
from lolcoach.config import Settings, get_settings
from lolcoach.dataprovider.opgg_mcp import OpggMcpClient, OpggMcpError
from lolcoach.dataprovider.typed_repr import TypedReprError
from lolcoach.values import as_float, as_int, as_str, opt_dict, opt_list

logger = logging.getLogger(__name__)

# Only the fields the coach actually reports. The full profile/guide payloads are
# tens of kilobytes; asking for less keeps the model's context (and the guard's
# surface) small.
_PROFILE_FIELDS = [
    "data.summoner.ladder_rank.{rank,total}",
    "data.summoner.league_stats[].{game_type,win,lose,play,is_hot_streak,is_veteran}",
    "data.summoner.league_stats[].tier_info.{tier,division,lp,level}",
    "data.summoner.most_champions.{game_type,season_id,play,win,lose}",
]

_CacheKey = tuple[str, tuple[tuple[str, str], ...]]


class _ToolClient(Protocol):
    """Just enough of :class:`OpggMcpClient` to fetch one tool result."""

    def __enter__(self) -> _ToolClient: ...

    def __exit__(self, *exc: object) -> None: ...

    def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...


class OpggLiveProvider:
    """Fetches a summoner profile or a lane matchup guide, cached and throttled."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], _ToolClient] | None = None,
    ) -> None:
        self._url = settings.opgg_mcp_url
        self._timeout = settings.live_timeout
        self._ttl = settings.live_cache_ttl
        self._min_interval = settings.live_min_interval
        self._region = settings.live_region
        self._lock = threading.Lock()
        self._cache: dict[_CacheKey, tuple[float, dict[str, object]]] = {}
        self._last_call = 0.0
        self._client_factory = client_factory or self._new_client

    def _new_client(self) -> _ToolClient:
        return OpggMcpClient(self._url, timeout=self._timeout, max_retries=1)

    def summoner_profile(self, riot_id: str, region: str = "") -> dict[str, object]:
        """Rank, LP, and record for a public ``gameName#tagLine`` Riot ID.

        Champion names are deliberately **not** returned: the guard whitelists only
        what a tool is entitled to source, so surfacing the player's champion pool
        would either license naming champions no tool measured, or (under the strict
        guard) discard every answer that mentions the player's own picks.
        """
        game_name, tag_line = _split_riot_id(riot_id)
        if not game_name or not tag_line:
            raise LiveDataError(f"expected a Riot ID like 'Name#TAG', got {riot_id!r}")
        root = self._call(
            "lol_get_summoner_profile",
            {
                "game_name": game_name,
                "tag_line": tag_line,
                "region": (region or self._region).upper(),
                "desired_output_fields": _PROFILE_FIELDS,
            },
        )
        return _profile_payload(opt_dict(root.get("data")), riot_id, region or self._region)

    def lane_matchup(self, champion: str, opponent: str, role: str) -> dict[str, object]:
        """The written matchup tip plus the lane stats for one specific pair."""
        root = self._call(
            "lol_get_lane_matchup_guide",
            {
                "my_champion": champion,
                "opponent_champion": opponent,
                "position": role,
            },
        )
        return _matchup_payload(root, champion, opponent, role)

    # -- internals --------------------------------------------------------

    def _call(self, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        key: _CacheKey = (tool, tuple(sorted((k, str(v)) for k, v in arguments.items())))
        with self._lock:
            cached = self._cache.get(key)
            now = time.monotonic()
            if cached is not None and now - cached[0] < self._ttl:
                logger.debug("Live lookup cache hit", extra={"data": {"tool": tool}})
                return cached[1]
            self._throttle()
            try:
                data = self._fetch(tool, arguments)
            except (OpggMcpError, TypedReprError) as exc:
                logger.warning(
                    "Live lookup failed", extra={"data": {"tool": tool, "error": str(exc)}}
                )
                raise LiveDataError(f"{tool} failed: {exc}") from exc
            self._last_call = time.monotonic()
            self._prune(self._last_call)
            self._cache[key] = (self._last_call, data)
            return data

    def _fetch(self, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        # A fresh session per call: the MCP session id is server state, and this
        # runs on a threadpool, so sharing one client would couple concurrent
        # requests for no real gain (the cache absorbs repeats).
        with self._client_factory() as client:
            return opt_dict(client.call_tool(tool, arguments))

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _prune(self, now: float) -> None:
        for stale in [key for key, (at, _) in self._cache.items() if now - at >= self._ttl]:
            del self._cache[stale]


@lru_cache(maxsize=1)
def build_live_provider() -> OpggLiveProvider:
    """The process-wide live provider.

    Must be a singleton: the TTL cache and the min-interval gate live on the
    instance, so a per-request provider would cache nothing and throttle nothing.
    It takes no arguments because :class:`Settings` carries a dict and is therefore
    unhashable; the settings themselves are already ``lru_cache``d.
    """
    return OpggLiveProvider(get_settings())


def _split_riot_id(riot_id: str) -> tuple[str, str]:
    name, _, tag = riot_id.partition("#")
    return name.strip(), tag.strip()


def _profile_payload(
    data: dict[str, object], riot_id: str, region: str
) -> dict[str, object]:
    summoner = opt_dict(data.get("summoner"))
    queues: list[dict[str, object]] = []
    for entry in opt_list(summoner.get("league_stats")):
        stat = opt_dict(entry)
        info = opt_dict(stat.get("tier_info"))
        tier = as_str(info.get("tier"))
        if not tier:
            continue  # an unranked queue carries nothing to report
        queues.append(
            {
                "queue": as_str(stat.get("game_type")),
                "tier": tier,
                "division": as_int(info.get("division"), 0) or None,
                "lp": as_int(info.get("lp"), 0) or None,
                "wins": as_int(stat.get("win"), 0),
                "losses": as_int(stat.get("lose"), 0),
                "hot_streak": bool(stat.get("is_hot_streak")),
            }
        )
    season = opt_dict(summoner.get("most_champions"))
    ladder = opt_dict(summoner.get("ladder_rank"))
    return {
        "riot_id": riot_id,
        "region": region.upper(),
        "ladder": {
            "rank": as_int(ladder.get("rank"), 0) or None,
            "total": as_int(ladder.get("total"), 0) or None,
        },
        "queues": queues,
        "season": {
            "queue": as_str(season.get("game_type")),
            "season_id": as_int(season.get("season_id"), 0) or None,
            "games": as_int(season.get("play"), 0),
            "wins": as_int(season.get("win"), 0),
            "losses": as_int(season.get("lose"), 0),
        },
    }


def _matchup_payload(
    root: dict[str, object], champion: str, opponent: str, role: str
) -> dict[str, object]:
    data = opt_dict(root.get("data"))
    summary = opt_dict(data.get("summary"))
    stats = _lane_stats(summary, role)
    return {
        "champion": as_str(root.get("my_champion")) or champion,
        "opponent": as_str(root.get("opponent_champion")) or opponent,
        "position": as_str(root.get("position")) or role,
        "tip": as_str(data.get("opponent_champion_tip")),
        "solo_kill_advantage": as_str(data.get("lane_solo_kill_advantage_champion")),
        "lane_advantage": as_str(data.get("lane_advantage_champion")),
        "play_style": as_str(data.get("recommended_play_style")),
        "champion_win_rate": as_float(stats.get("win_rate")),
        "games": as_int(stats.get("play"), 0),
    }


def _lane_stats(summary: dict[str, object], role: str) -> dict[str, object]:
    """The champion's own stats in ``role``, not their all-lane average."""
    for entry in opt_list(summary.get("positions")):
        position = opt_dict(entry)
        if as_str(position.get("name")).lower() == role.lower():
            return opt_dict(position.get("stats"))
    return {}
