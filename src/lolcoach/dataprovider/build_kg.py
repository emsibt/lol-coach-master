"""Build the YAML knowledge graph from the SQLite snapshot.

Fully generated — no hand-authored reasons. Every relation's ``weight`` is a
measured win-rate delta scaled by sample-size confidence, and its ``reason`` is a
statistical sentence. This is the accepted trade of the OP.GG-only strategy:
edges explain *how much*, with the sample to back it, rather than *why*.

Outputs (into ``knowledge/seed/``)::

    champions.yaml   # roster, per-lane meta stats, role classes
    relations.yaml   # counter and synergy edges
    patches.yaml     # provenance: patch, source, generated_at, counts

Counter direction is derived from the win rate, not trusted from the tool's
``strong``/``weak`` label, so the two perspectives of a matchup collapse to one
edge pointing at the winner.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import yaml

from lolcoach.constants import (
    EVEN_WIN_RATE,
    LANES,
    MIN_EDGE_DELTA,
    MIN_EDGE_GAMES,
    SAMPLE_FULL_CONFIDENCE,
)
from lolcoach.values import as_int, opt_int

logger = logging.getLogger(__name__)


def build_knowledge_graph(
    conn: sqlite3.Connection,
    seed_dir: Path,
    *,
    patch: str = "unknown",
    source: str = "opgg-mcp",
) -> dict[str, int]:
    """Read the snapshot, write the three YAML files, return counts."""
    slug_by_id: dict[int, str] = {}
    champions = _champions(conn, slug_by_id)
    relations = _relations(conn, slug_by_id)

    generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    counts = {
        "champion_count": len(champions),
        "relation_count": len(relations),
        "counter_count": sum(1 for r in relations if r["type"] == "counter"),
        "synergy_count": sum(1 for r in relations if r["type"] == "synergy"),
    }
    meta = {
        "patch": patch,
        "source": source,
        "generated_at": generated_at,
        **counts,
    }

    seed_dir.mkdir(parents=True, exist_ok=True)
    _dump(seed_dir / "champions.yaml", {"meta": meta, "champions": champions})
    _dump(seed_dir / "relations.yaml", {"meta": meta, "relations": relations})
    _dump(seed_dir / "patches.yaml", {"meta": meta})

    logger.info("Knowledge graph built", extra={"data": {"seed_dir": str(seed_dir), **counts}})
    return counts


def _champions(conn: sqlite3.Connection, slug_by_id: dict[int, str]) -> list[dict[str, object]]:
    stats: dict[tuple[int, str], sqlite3.Row] = {}
    for row in conn.execute("SELECT * FROM champion_stats"):
        stats[(int(row["champion_id"]), str(row["position"]))] = row
    roles: dict[tuple[int, str], list[str]] = {}
    for row in conn.execute("SELECT * FROM champion_roles ORDER BY role"):
        roles.setdefault((int(row["champion_id"]), str(row["position"])), []).append(
            str(row["role"])
        )

    champions: list[dict[str, object]] = []
    for row in conn.execute(
        "SELECT champion_id, key, name, slug FROM champions ORDER BY champion_id"
    ):
        champion_id = int(row["champion_id"])
        slug = str(row["slug"])
        slug_by_id[champion_id] = slug
        positions: list[dict[str, object]] = []
        for position in LANES:
            stat = stats.get((champion_id, position))
            if stat is None:
                continue
            positions.append(
                {
                    "position": position,
                    "play": int(stat["play"]),
                    "win_rate": round(float(stat["win_rate"]), 4),
                    "pick_rate": round(float(stat["pick_rate"]), 4),
                    "ban_rate": round(float(stat["ban_rate"]), 4),
                    "tier": opt_int(stat["tier"]),
                    "rank": opt_int(stat["rank"]),
                    "roles": roles.get((champion_id, position), []),
                }
            )
        champions.append(
            {
                "slug": slug,
                "name": str(row["name"]),
                "champion_id": champion_id,
                "key": str(row["key"]),
                "positions": positions,
            }
        )
    return champions


def _relations(
    conn: sqlite3.Connection, slug_by_id: dict[int, str]
) -> list[dict[str, object]]:
    counters = _counter_edges(conn, slug_by_id)
    synergies = _synergy_edges(conn, slug_by_id)
    relations = counters + synergies
    relations.sort(
        key=lambda r: (
            str(r["type"]), str(r["position"]), str(r["source"]), str(r["target"])
        )
    )
    return relations


def _counter_edges(
    conn: sqlite3.Connection, slug_by_id: dict[int, str]
) -> list[dict[str, object]]:
    best: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in conn.execute(
        "SELECT champion_id, opponent_id, position, play, my_win_rate FROM matchups"
    ):
        champion_id = int(row["champion_id"])
        opponent_id = int(row["opponent_id"])
        my_win_rate = float(row["my_win_rate"])
        games = int(row["play"])
        source_slug = slug_by_id.get(champion_id)
        target_slug = slug_by_id.get(opponent_id)
        if source_slug is None or target_slug is None or source_slug == target_slug:
            continue
        if my_win_rate >= EVEN_WIN_RATE:
            winner, loser, win_rate = source_slug, target_slug, my_win_rate
        else:
            winner, loser, win_rate = target_slug, source_slug, 1.0 - my_win_rate
        delta = win_rate - EVEN_WIN_RATE
        if games < MIN_EDGE_GAMES or delta < MIN_EDGE_DELTA:
            continue
        position = str(row["position"])
        key = (winner, loser, position)
        existing = best.get(key)
        if existing is not None and as_int(existing["games"]) >= games:
            continue
        best[key] = {
            "source": winner,
            "target": loser,
            "type": "counter",
            "position": position,
            "weight": _weight(win_rate, games),
            "confidence": _confidence(games),
            "games": games,
            "win_rate": round(win_rate, 4),
            "reason": _counter_reason(winner, loser, position, win_rate, games),
        }
    return list(best.values())


def _synergy_edges(
    conn: sqlite3.Connection, slug_by_id: dict[int, str]
) -> list[dict[str, object]]:
    best: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in conn.execute(
        "SELECT champion_id, ally_id, position, play, win_rate FROM synergies"
    ):
        a = slug_by_id.get(int(row["champion_id"]))
        b = slug_by_id.get(int(row["ally_id"]))
        if a is None or b is None or a == b:
            continue
        win_rate = float(row["win_rate"])
        games = int(row["play"])
        delta = win_rate - EVEN_WIN_RATE
        if games < MIN_EDGE_GAMES or abs(delta) < MIN_EDGE_DELTA:
            continue
        source, target = sorted((a, b))
        position = str(row["position"])
        key = (source, target, position)
        existing = best.get(key)
        if existing is not None and as_int(existing["games"]) >= games:
            continue
        best[key] = {
            "source": source,
            "target": target,
            "type": "synergy",
            "position": position,
            "weight": _weight(win_rate, games),
            "confidence": _confidence(games),
            "games": games,
            "win_rate": round(win_rate, 4),
            "reason": _synergy_reason(source, target, position, win_rate, games),
        }
    return list(best.values())


def _weight(win_rate: float, games: int) -> float:
    return round((win_rate - EVEN_WIN_RATE) * _confidence(games), 4)


def _confidence(games: int) -> float:
    return round(min(1.0, games / SAMPLE_FULL_CONFIDENCE), 4)


def _counter_reason(source: str, target: str, position: str, win_rate: float, games: int) -> str:
    return f"{source} counters {target} in {position}: {win_rate:.1%} over {games:,} games"


def _synergy_reason(source: str, target: str, position: str, win_rate: float, games: int) -> str:
    return f"{source} + {target} in {position}: {win_rate:.1%} over {games:,} games"


def _dump(path: Path, payload: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True, width=100)
