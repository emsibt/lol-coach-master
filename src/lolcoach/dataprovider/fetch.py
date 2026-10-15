"""Throttled, resumable OP.GG fetch into the SQLite snapshot.

Flow:

1. ``lol_list_champions`` — the full roster (id, key, name) with slugs.
2. ``lol_list_lane_meta_champions`` (one call per lane) — per-lane meta stats and,
   crucially, champion→lane membership, so we know which (champion, position)
   pairs are worth analyzing.
3. ``lol_get_champion_analysis`` (one call per member pair) — matchups and
   synergies, stored from the analyzed champion's own perspective; its
   recommended items, runes, and skill order are captured in the same call.
4. ``lol_list_champion_details`` (batches of 10) + ``lol_list_items`` (one call) —
   the **reference** material: tips, abilities, lore, and the item catalog. This
   is the only extra cost (about 18 calls for the full roster), not a second pull.

Resumability: a pair already recorded in ``analysis_done`` is skipped, and a
champion already recorded in ``reference_done`` is not re-fetched, so an
interrupted multi-hour pull can simply be re-run. Politeness: a configurable
delay is applied before each network call.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol, TypeVar

from lolcoach.constants import LANES
from lolcoach.dataprovider import store
from lolcoach.dataprovider.opgg_mcp import OpggMcpError
from lolcoach.dataprovider.typed_repr import TypedReprError
from lolcoach.text import slugify, upper_snake
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
    # Reference builds: captured in the call we already make, so the only cost is
    # payload size. Each item group is a single object (not a list of builds).
    # Summoner spells and stat shards are deliberately *not* requested: OP.GG
    # returns bare spell/rune ids for them (``ids_names: [4, 12]``), which would
    # materialize as meaningless numbers rather than names.
    "data.core_items.{ids[],ids_names[],pick_rate,play,win}",
    "data.boots.{ids[],ids_names[],pick_rate,play,win}",
    "data.starter_items.{ids[],ids_names[],pick_rate,play,win}",
    "data.runes.{primary_page_name,primary_rune_names[],secondary_page_name,"
    "secondary_rune_names[],pick_rate,play}",
    "data.skill_masteries.builds[].{order[],pick_rate,play}",
    "data.skills.{order[],pick_rate,play}",
]

_DETAIL_FIELDS = [
    "data.champions[].{id,key,name,lore,blurb,tags[],ally_tips[],enemy_tips[]}",
    "data.champions[].passive.{name,description}",
    "data.champions[].spells[].{key,name,description}",
]

# ``lol_list_champion_details`` accepts at most 10 champions per call.
_REFERENCE_BATCH = 10

_T = TypeVar("_T")

# Reference item groups, keyed by the analysis field they come from.
_ITEM_GROUPS = ("starter_items", "boots", "core_items")
_ITEM_TYPE = {
    "starter_items": "starter",
    "boots": "boots",
    "core_items": "core",
}


class ToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...


@dataclass(slots=True)
class FetchReport:
    champions: int = 0
    stat_rows: int = 0
    role_rows: int = 0
    matchup_rows: int = 0
    synergy_rows: int = 0
    build_rows: int = 0
    rune_rows: int = 0
    reference_rows: int = 0
    item_rows: int = 0
    analyzed_pairs: int = 0
    skipped_pairs: int = 0
    errors: int = 0


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
    _load_reference(caller, conn, roster, report,
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
        by_name = {**arguments, "champion": upper_snake(name)}
        return _analysis_data(caller.call_tool("lol_get_champion_analysis", by_name))
    except OpggMcpError:
        logger.warning(
            "Champion name form rejected; retrying with key",
            extra={"data": {"champion": name, "key": key, "position": position}},
        )
        by_key = {**arguments, "champion": upper_snake(key)}
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

    _store_builds(conn, champion_id, position, data, report)


def _store_builds(
    conn: sqlite3.Connection,
    champion_id: int,
    position: str,
    data: dict[str, object],
    report: FetchReport,
) -> None:
    """Store the recommended item groups, rune page, and skill order.

    Item groups are single objects of positionally-paired ``ids``/``ids_names``.
    Skill order comes from ``skill_masteries.builds`` (the popular orders, best
    first); the legacy ``skills.order`` is the fallback.
    """
    for field in _ITEM_GROUPS:
        group = opt_dict(data.get(field))
        if not group:
            continue
        play = as_int(group.get("play"))
        win = as_int(group.get("win"))
        pick_rate = as_float(group.get("pick_rate"))
        for ordinal, (item_id, name) in enumerate(_pairs(group)):
            store.upsert_champion_build(
                conn, champion_id, position, _ITEM_TYPE[field], ordinal,
                item_id=item_id, item_name=name, play=play, win=win, pick_rate=pick_rate,
            )
            report.build_rows += 1

    runes = opt_dict(data.get("runes"))
    # Slot bases keep the page order (primary, then secondary) intact under a
    # plain ``ORDER BY slot`` at build time.
    for page, rune_key, base in (
        (as_str(runes.get("primary_page_name")), "primary_rune_names", 0),
        (as_str(runes.get("secondary_page_name")), "secondary_rune_names", 100),
    ):
        for index, rune in enumerate(opt_list(runes.get(rune_key))):
            name = as_str(rune)
            if not name:
                continue
            store.upsert_champion_rune(conn, champion_id, position, page, base + index, name)
            report.rune_rows += 1

    order = _skill_order(data)
    for ordinal, letter in enumerate(order):
        store.upsert_champion_build(
            conn, champion_id, position, "skill", ordinal, item_name=letter
        )
        report.build_rows += 1


def _skill_order(data: dict[str, object]) -> list[str]:
    for build in opt_list(opt_dict(data.get("skill_masteries")).get("builds")):
        order = [as_str(item) for item in opt_list(opt_dict(build).get("order"))]
        if any(order):
            return [item for item in order if item]
    return [as_str(item) for item in opt_list(opt_dict(data.get("skills")).get("order")) if item]


def _pairs(group: dict[str, object]) -> list[tuple[int, str]]:
    """Positionally pair a group's ``ids`` with its ``ids_names``."""
    ids = opt_list(group.get("ids"))
    names = opt_list(group.get("ids_names"))
    pairs: list[tuple[int, str]] = []
    for index in range(max(len(ids), len(names))):
        item_id = as_int(ids[index]) if index < len(ids) else 0
        name = as_str(names[index]) if index < len(names) else ""
        if item_id or name:
            pairs.append((item_id, name))
    return pairs


def _load_reference(
    caller: ToolCaller,
    conn: sqlite3.Connection,
    roster: dict[int, tuple[str, str]],
    report: FetchReport,
    *,
    interval: float,
    limit: int | None,
    sleep: Callable[[float], None],
) -> None:
    """Load per-champion tips/abilities/lore, then the item catalog.

    A champion is recorded even when the server returns no tips for it, so an
    empty-but-known champion is not refetched on the next run.
    """
    targets = sorted(roster.items())
    if limit is not None:
        targets = targets[:limit]
    pending = [
        (champion_id, key, name)
        for champion_id, (key, name) in targets
        if not store.reference_done(conn, champion_id)
    ]
    by_name = {name.lower(): champion_id for champion_id, _key, name in pending}
    for batch in _batched(pending, _REFERENCE_BATCH):
        sleep(interval)
        try:
            root = _details_batch(caller, batch)
        except (OpggMcpError, TypedReprError):
            logger.exception(
                "Champion details failed; leaving reference data resumable",
                extra={"data": {"champions": [name for _id, _key, name in batch]}},
            )
            report.errors += 1
            continue
        for item in opt_list(as_dict(root.get("data")).get("champions")):
            d = opt_dict(item)
            champion_id = opt_int(d.get("id"))
            if champion_id is None or champion_id not in roster:
                champion_id = by_name.get(as_str(d.get("name")).lower())
            if champion_id is None or champion_id not in roster:
                logger.warning(
                    "Champion details returned an unknown champion",
                    extra={"data": {"champion": as_str(d.get("name"))}},
                )
                continue
            store.upsert_champion_reference(
                conn,
                champion_id,
                lore=as_str(d.get("lore")),
                blurb=as_str(d.get("blurb")),
                tags=[as_str(tag) for tag in opt_list(d.get("tags")) if as_str(tag)],
                ally_tips=[as_str(t) for t in opt_list(d.get("ally_tips")) if as_str(t)],
                enemy_tips=[as_str(t) for t in opt_list(d.get("enemy_tips")) if as_str(t)],
                abilities=_abilities(d),
                updated_at=_now_iso(),
            )
            report.reference_rows += 1
        for champion_id, _key, _name in batch:
            store.mark_reference_done(conn, champion_id)
        conn.commit()
        logger.info(
            "Champion reference loaded",
            extra={"data": {"rows": report.reference_rows}},
        )
    _load_items(caller, conn, report, interval=interval, sleep=sleep)


def _details_batch(caller: ToolCaller, batch: list[tuple[int, str, str]]) -> dict[str, object]:
    """One ``lol_list_champion_details`` call, addressed by display name.

    OP.GG's identifier is the *display name* squashed to UPPER_SNAKE — ``Cho'Gath``
    is ``CHOGATH`` and ``Dr. Mundo`` is ``DR_MUNDO`` — which is not the Riot key
    uppercased (``DRMUNDO`` is rejected). The key is a fallback.
    """
    arguments = {"desired_output_fields": _DETAIL_FIELDS}
    try:
        names = [upper_snake(name) for _id, _key, name in batch]
        call: dict[str, object] = {**arguments, "champions": names}
        return as_dict(caller.call_tool("lol_list_champion_details", call))
    except OpggMcpError:
        logger.warning(
            "Champion details rejected the name form; retrying with keys",
            extra={"data": {"champions": [name for _id, _key, name in batch]}},
        )
        keys = [upper_snake(key) for _id, key, _name in batch]
        return as_dict(
            caller.call_tool("lol_list_champion_details", {**arguments, "champions": keys})
        )


def _abilities(detail: dict[str, object]) -> list[dict[str, str]]:
    """The passive plus Q/W/E/R, each as ``{slot, name, description}``."""
    abilities: list[dict[str, str]] = []
    passive = opt_dict(detail.get("passive"))
    if passive:
        abilities.append(
            {
                "slot": "P",
                "name": as_str(passive.get("name")),
                "description": as_str(passive.get("description")),
            }
        )
    for index, spell in enumerate(opt_list(detail.get("spells"))):
        s = opt_dict(spell)
        abilities.append(
            {
                "slot": as_str(s.get("key")) or "QWER"[index : index + 1],
                "name": as_str(s.get("name")),
                "description": as_str(s.get("description")),
            }
        )
    return abilities


def _load_items(
    caller: ToolCaller,
    conn: sqlite3.Connection,
    report: FetchReport,
    *,
    interval: float,
    sleep: Callable[[float], None],
) -> None:
    """One call for the whole Summoner's Rift item catalog.

    ``lol_list_items`` returns plain JSON, unlike the typed-repr tools; the MCP
    client parses both (``opgg_mcp._parse_result_text``).
    """
    sleep(interval)
    try:
        root = as_dict(caller.call_tool("lol_list_items", {"map": "SUMMONERS_RIFT"}))
    except (OpggMcpError, TypedReprError):
        logger.exception("Item catalog fetch failed")
        report.errors += 1
        return
    for item in opt_list(as_dict(root.get("data")).get("items")):
        d = opt_dict(item)
        item_id = as_int(d.get("item_id"), -1)
        name = as_str(d.get("name"))
        if item_id < 0 or not name:
            continue
        store.upsert_item(
            conn,
            item_id,
            name=name,
            description=as_str(d.get("description")),
            gold_total=as_int(d.get("gold_total")),
            builds_into=[as_int(i) for i in opt_list(d.get("into_items"))],
            builds_from=[as_int(i) for i in opt_list(d.get("from_items"))],
        )
        report.item_rows += 1
    conn.commit()
    logger.info("Item catalog loaded", extra={"data": {"items": report.item_rows}})


def _batched(items: list[_T], size: int) -> list[list[_T]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
