"""Throttled, resumable OP.GG fetch into the SQLite snapshot.

Flow:

1. ``lol_list_champions`` — the full roster (id, key, name) with slugs.
2. ``lol_list_lane_meta_champions`` (one call per lane) — per-lane meta stats and,
   crucially, champion→lane membership, so we know which (champion, position)
   pairs are worth analyzing.
3. ``lol_get_champion_analysis`` (one call per member pair) — matchups and
   synergies, stored from the analyzed champion's own perspective.

Resumability: a pair already recorded in ``analysis_done`` is skipped, so an
interrupted multi-hour pull can simply be re-run. Politeness: a configurable
delay is applied before each analysis call.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from lolcoach.constants import LANES
from lolcoach.dataprovider import store
from lolcoach.dataprovider.opgg_mcp import OpggMcpError
from lolcoach.dataprovider.typed_repr import TypedReprError
from lolcoach.text import slugify
from lolcoach.values import (
    as_dict,
    as_float,
    as_int,
    as_list,
    as_str,
    opt_dict,
    opt_int,
    opt_list,
)

logger = logging.getLogger(__name__)

_ROSTER_FIELDS = ["data.champions[].{champion_id,key,name}"]

# OP.GG also lists alternate-mode champion variants with ids at/above this base.
_VARIANT_CHAMPION_ID = 60000
_ANALYSIS_FIELDS = [
    "data.summary.average_stats.{win_rate,pick_rate,ban_rate,tier,rank,play}",
    "data.summary.positions[].{name,roles[].{name}}",
    "data.strong_counters[].{champion_name,counter_win_rate,my_win_rate,play}",
    "data.weak_counters[].{champion_name,counter_win_rate,my_win_rate,play}",
    "data.synergies.{top,mid,jungle,adc,support}[].{synergy_champion_name,win_rate,play}",
]


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...


@dataclass(slots=True)
class FetchReport:
    champions: int = 0
    stat_rows: int = 0
    role_rows: int = 0
    matchup_rows: int = 0
    synergy_rows: int = 0
    analyzed_pairs: int = 0
    skipped_pairs: int = 0
    errors: int = 0


def _upper_snake(value: str) -> str:
    """``"Twisted Fate"`` -> ``"TWISTED_FATE"``, ``"Kai'Sa"`` -> ``"KAISA"``."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", value)).strip("_").upper()


def _lane_fields(position: str) -> list[str]:
    return [
        f"data.positions.{position}[]."
        "{champion,play,win_rate,pick_rate,ban_rate,tier,rank}"
    ]


def fetch_snapshot(
    caller: ToolCaller,
    conn: sqlite3.Connection,
    *,
    interval: float = 1.2,
    limit: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchReport:
    """Pull roster, meta, matchups, and synergies into ``conn``."""
    report = FetchReport()

    roster = _load_roster(caller, conn, report)
    name_index = store.name_to_id(conn)
    membership = _load_lane_meta(caller, conn, name_index, report, interval=interval, sleep=sleep)
    _load_analyses(caller, conn, roster, name_index, membership, report,
                   interval=interval, limit=limit, sleep=sleep)

    store.set_meta(conn, "fetched_at", _now_iso())
    store.set_meta(conn, "source", "opgg-mcp")
    conn.commit()
    logger.info("Snapshot fetch complete", extra={"data": asdict(report)})
    return report


def _load_roster(
    caller: ToolCaller, conn: sqlite3.Connection, report: FetchReport
) -> dict[int, tuple[str, str]]:
    root = as_dict(
        caller.call_tool("lol_list_champions", {"desired_output_fields": _ROSTER_FIELDS})
    )
    rows = as_list(as_dict(root.get("data")).get("champions"))
    roster: dict[int, tuple[str, str]] = {}
    for item in rows:
        d = opt_dict(item)
        champion_id = as_int(d.get("champion_id"), -1)
        name = as_str(d.get("name"))
        key = as_str(d.get("key"))
        if champion_id < 0 or not name:
            continue
        # Skip alternate-mode variants (e.g. "Jade_Annie", id >= 60000); they
        # duplicate the real champions and never appear in a standard draft.
        if champion_id >= _VARIANT_CHAMPION_ID or key.startswith("Jade_"):
            continue
        store.upsert_champion(conn, champion_id, key or name, name, slugify(name))
        roster[champion_id] = (key or name, name)
        report.champions += 1
    conn.commit()
    logger.info("Roster loaded", extra={"data": {"champions": report.champions}})
    return roster


def _load_lane_meta(
    caller: ToolCaller,
    conn: sqlite3.Connection,
    name_index: dict[str, int],
    report: FetchReport,
    *,
    interval: float,
    sleep: Callable[[float], None],
) -> dict[int, list[str]]:
    membership: dict[int, list[str]] = {}
    for position in LANES:
        root = as_dict(
            caller.call_tool(
                "lol_list_lane_meta_champions",
                {"position": position, "desired_output_fields": _lane_fields(position)},
            )
        )
        positions = as_dict(as_dict(root.get("data")).get("positions"))
        for item in opt_list(positions.get(position)):
            d = opt_dict(item)
            champion_id = name_index.get(as_str(d.get("champion")).lower())
            if champion_id is None:
                logger.warning(
                    "Lane-meta champion not in roster; skipping",
                    extra={"data": {"champion": as_str(d.get("champion")), "position": position}},
                )
                continue
            store.upsert_champion_stats(
                conn,
                champion_id,
                position,
                play=as_int(d.get("play")),
                win_rate=as_float(d.get("win_rate")),
                pick_rate=as_float(d.get("pick_rate")),
                ban_rate=as_float(d.get("ban_rate")),
                tier=opt_int(d.get("tier")),
                rank=opt_int(d.get("rank")),
            )
            report.stat_rows += 1
            membership.setdefault(champion_id, [])
            if position not in membership[champion_id]:
                membership[champion_id].append(position)
        conn.commit()
        logger.info(
            "Lane meta loaded", extra={"data": {"position": position, "rows": report.stat_rows}}
        )
        sleep(interval)
    return membership


def _load_analyses(
    caller: ToolCaller,
    conn: sqlite3.Connection,
    roster: dict[int, tuple[str, str]],
    name_index: dict[str, int],
    membership: dict[int, list[str]],
    report: FetchReport,
    *,
    interval: float,
    limit: int | None,
    sleep: Callable[[float], None],
) -> None:
    targets = sorted(membership.items())
    if limit is not None:
        targets = targets[:limit]
    for champion_id, positions in targets:
        key, name = roster.get(champion_id, ("", f"#{champion_id}"))
        for position in [p for p in LANES if p in positions]:
            if store.analysis_done(conn, champion_id, position):
                report.skipped_pairs += 1
                continue
            sleep(interval)
            try:
                data = _call_analysis(caller, name, key, position)
            except (OpggMcpError, TypedReprError):
                logger.exception(
                    "Champion analysis failed; leaving pair resumable",
                    extra={"data": {"champion": name, "position": position}},
                )
                report.errors += 1
                continue
            _store_analysis(conn, champion_id, position, data, name_index, report)
            store.mark_analysis_done(conn, champion_id, position)
            conn.commit()
            report.analyzed_pairs += 1
            logger.info(
                "Champion analyzed",
                extra={"data": {"champion": name, "position": position}},
            )


def _call_analysis(caller: ToolCaller, name: str, key: str, position: str) -> dict[str, object]:
    arguments: dict[str, object] = {
        "position": position,
        "game_mode": "ranked",
        "desired_output_fields": _ANALYSIS_FIELDS,
    }
    try:
        by_name = {**arguments, "champion": _upper_snake(name)}
        return _analysis_data(caller.call_tool("lol_get_champion_analysis", by_name))
    except OpggMcpError:
        logger.warning(
            "Champion name form rejected; retrying with key",
            extra={"data": {"champion": name, "key": key, "position": position}},
        )
        by_key = {**arguments, "champion": _upper_snake(key)}
        return _analysis_data(caller.call_tool("lol_get_champion_analysis", by_key))


def _analysis_data(root: object) -> dict[str, object]:
    """Unwrap the tool root (``{data, _field_diagnostics}``) to the ``data`` object."""
    return as_dict(as_dict(root).get("data"))


def _store_analysis(
    conn: sqlite3.Connection,
    champion_id: int,
    position: str,
    data: dict[str, object],
    name_index: dict[str, int],
    report: FetchReport,
) -> None:
    for source_field, kind in (("strong_counters", "strong"), ("weak_counters", "weak")):
        for item in opt_list(data.get(source_field)):
            d = opt_dict(item)
            opponent_id = name_index.get(as_str(d.get("champion_name")).lower())
            if opponent_id is None:
                continue
            store.upsert_matchup(
                conn,
                champion_id,
                opponent_id,
                position,
                play=as_int(d.get("play")),
                my_win_rate=as_float(d.get("my_win_rate")),
                counter_win_rate=as_float(d.get("counter_win_rate")),
                kind=kind,
            )
            report.matchup_rows += 1

    synergies = opt_dict(data.get("synergies"))
    for role in LANES:
        for item in opt_list(synergies.get(role)):
            d = opt_dict(item)
            ally_id = name_index.get(as_str(d.get("synergy_champion_name")).lower())
            if ally_id is None:
                continue
            store.upsert_synergy(
                conn,
                champion_id,
                ally_id,
                position,
                play=as_int(d.get("play")),
                win_rate=as_float(d.get("win_rate")),
            )
            report.synergy_rows += 1

    summary = opt_dict(data.get("summary"))
    for position_info in opt_list(summary.get("positions")):
        p = opt_dict(position_info)
        pname = as_str(p.get("name")).lower()
        if pname not in LANES:
            continue
        for role_info in opt_list(p.get("roles")):
            role_name = as_str(opt_dict(role_info).get("name"))
            if not role_name:
                continue
            store.upsert_champion_role(conn, champion_id, pname, role_name, play=0, win_rate=0.0)
            report.role_rows += 1


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
