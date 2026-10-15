"""SQLite snapshot of OP.GG data.

The snapshot is **replaceable**: it is rebuilt from the OP.GG MCP server by
``lolcoach.dataprovider.fetch`` and exists only to serve the knowledge-graph
build and the engine. It is deliberately kept in its own database, separate from
the workflow's execution history (different lifecycle: refresh vs. preserve).

Direction convention
--------------------
``matchups`` stores one row per analyzed champion *from that champion's own
perspective*: ``champion_id`` is the champion whose analysis produced the row,
``opponent_id`` is the other champion, and ``my_win_rate`` is ``champion_id``'s
win rate in the head-to-head. Whether that means a counter or a weakness is
derived from the win rate, not stored, so the data stays symmetric and testable.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS champions (
    champion_id INTEGER PRIMARY KEY,
    key         TEXT NOT NULL,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS champion_stats (
    champion_id INTEGER NOT NULL,
    position    TEXT NOT NULL,
    play        INTEGER NOT NULL DEFAULT 0,
    win_rate    REAL NOT NULL DEFAULT 0,
    pick_rate   REAL NOT NULL DEFAULT 0,
    ban_rate    REAL NOT NULL DEFAULT 0,
    tier        INTEGER,
    rank        INTEGER,
    PRIMARY KEY (champion_id, position)
);
CREATE TABLE IF NOT EXISTS champion_roles (
    champion_id INTEGER NOT NULL,
    position    TEXT NOT NULL,
    role        TEXT NOT NULL,
    play        INTEGER NOT NULL DEFAULT 0,
    win_rate    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (champion_id, position, role)
);
CREATE TABLE IF NOT EXISTS matchups (
    champion_id      INTEGER NOT NULL,
    opponent_id      INTEGER NOT NULL,
    position         TEXT NOT NULL,
    play             INTEGER NOT NULL DEFAULT 0,
    my_win_rate      REAL NOT NULL DEFAULT 0,
    counter_win_rate REAL NOT NULL DEFAULT 0,
    kind             TEXT NOT NULL,
    PRIMARY KEY (champion_id, opponent_id, position)
);
CREATE TABLE IF NOT EXISTS synergies (
    champion_id INTEGER NOT NULL,
    ally_id     INTEGER NOT NULL,
    position    TEXT NOT NULL,
    play        INTEGER NOT NULL DEFAULT 0,
    win_rate    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (champion_id, ally_id, position)
);
CREATE TABLE IF NOT EXISTS analysis_done (
    champion_id INTEGER NOT NULL,
    position    TEXT NOT NULL,
    PRIMARY KEY (champion_id, position)
);
CREATE TABLE IF NOT EXISTS champion_reference (
    champion_id INTEGER PRIMARY KEY,
    lore        TEXT NOT NULL DEFAULT '',
    blurb       TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    ally_tips   TEXT NOT NULL DEFAULT '[]',
    enemy_tips  TEXT NOT NULL DEFAULT '[]',
    abilities   TEXT NOT NULL DEFAULT '[]',
    updated_at  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS champion_builds (
    champion_id INTEGER NOT NULL,
    position    TEXT NOT NULL,
    item_type   TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    item_id     INTEGER NOT NULL DEFAULT 0,
    item_name   TEXT NOT NULL DEFAULT '',
    play        INTEGER NOT NULL DEFAULT 0,
    win         INTEGER NOT NULL DEFAULT 0,
    pick_rate   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (champion_id, position, item_type, ordinal)
);
CREATE TABLE IF NOT EXISTS champion_runes (
    champion_id INTEGER NOT NULL,
    position    TEXT NOT NULL,
    tree        TEXT NOT NULL,
    slot        INTEGER NOT NULL,
    rune        TEXT NOT NULL,
    PRIMARY KEY (champion_id, position, tree, slot)
);
CREATE TABLE IF NOT EXISTS items (
    item_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    gold_total  INTEGER NOT NULL DEFAULT 0,
    builds_into TEXT NOT NULL DEFAULT '[]',
    builds_from TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS reference_done (
    champion_id INTEGER PRIMARY KEY
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating parent dirs) a snapshot database with sane pragmas."""
    db_path = Path(path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def upsert_champion(
    conn: sqlite3.Connection, champion_id: int, key: str, name: str, slug: str
) -> None:
    conn.execute(
        "INSERT INTO champions(champion_id, key, name, slug) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(champion_id) DO UPDATE SET key = excluded.key, "
        "name = excluded.name, slug = excluded.slug",
        (champion_id, key, name, slug),
    )


def upsert_champion_stats(
    conn: sqlite3.Connection,
    champion_id: int,
    position: str,
    *,
    play: int,
    win_rate: float,
    pick_rate: float,
    ban_rate: float,
    tier: int | None,
    rank: int | None,
) -> None:
    conn.execute(
        "INSERT INTO champion_stats"
        "(champion_id, position, play, win_rate, pick_rate, ban_rate, tier, rank) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(champion_id, position) DO UPDATE SET play = excluded.play, "
        "win_rate = excluded.win_rate, pick_rate = excluded.pick_rate, "
        "ban_rate = excluded.ban_rate, tier = excluded.tier, rank = excluded.rank",
        (champion_id, position, play, win_rate, pick_rate, ban_rate, tier, rank),
    )


def upsert_champion_role(
    conn: sqlite3.Connection,
    champion_id: int,
    position: str,
    role: str,
    *,
    play: int,
    win_rate: float,
) -> None:
    conn.execute(
        "INSERT INTO champion_roles(champion_id, position, role, play, win_rate) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(champion_id, position, role) DO UPDATE SET "
        "play = excluded.play, win_rate = excluded.win_rate",
        (champion_id, position, role, play, win_rate),
    )


def upsert_matchup(
    conn: sqlite3.Connection,
    champion_id: int,
    opponent_id: int,
    position: str,
    *,
    play: int,
    my_win_rate: float,
    counter_win_rate: float,
    kind: str,
) -> None:
    conn.execute(
        "INSERT INTO matchups"
        "(champion_id, opponent_id, position, play, my_win_rate, counter_win_rate, kind) "
        "VALUES(?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(champion_id, opponent_id, position) DO UPDATE SET "
        "play = excluded.play, my_win_rate = excluded.my_win_rate, "
        "counter_win_rate = excluded.counter_win_rate, kind = excluded.kind",
        (champion_id, opponent_id, position, play, my_win_rate, counter_win_rate, kind),
    )


def upsert_synergy(
    conn: sqlite3.Connection,
    champion_id: int,
    ally_id: int,
    position: str,
    *,
    play: int,
    win_rate: float,
) -> None:
    conn.execute(
        "INSERT INTO synergies(champion_id, ally_id, position, play, win_rate) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(champion_id, ally_id, position) DO UPDATE SET "
        "play = excluded.play, win_rate = excluded.win_rate",
        (champion_id, ally_id, position, play, win_rate),
    )


def mark_analysis_done(conn: sqlite3.Connection, champion_id: int, position: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO analysis_done(champion_id, position) VALUES(?, ?)",
        (champion_id, position),
    )


def analysis_done(conn: sqlite3.Connection, champion_id: int, position: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM analysis_done WHERE champion_id = ? AND position = ?",
        (champion_id, position),
    ).fetchone()
    return row is not None


def mark_reference_done(conn: sqlite3.Connection, champion_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO reference_done(champion_id) VALUES(?)", (champion_id,)
    )


def reference_done(conn: sqlite3.Connection, champion_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM reference_done WHERE champion_id = ?", (champion_id,)
    ).fetchone()
    return row is not None


def decode_list(value: object) -> list[object]:
    """Decode a JSON-array column written by the reference upserts.

    Returns ``[]`` for anything unexpected: reference data is advisory, so a
    corrupted blob should degrade to "no data", never abort a build.
    """
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed JSON column in the snapshot")
        return []
    return parsed if isinstance(parsed, list) else []


def upsert_champion_reference(
    conn: sqlite3.Connection,
    champion_id: int,
    *,
    lore: str,
    blurb: str,
    tags: list[str],
    ally_tips: list[str],
    enemy_tips: list[str],
    abilities: list[dict[str, str]],
    updated_at: str,
) -> None:
    conn.execute(
        "INSERT INTO champion_reference"
        "(champion_id, lore, blurb, tags, ally_tips, enemy_tips, abilities, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(champion_id) DO UPDATE SET lore = excluded.lore, "
        "blurb = excluded.blurb, tags = excluded.tags, ally_tips = excluded.ally_tips, "
        "enemy_tips = excluded.enemy_tips, abilities = excluded.abilities, "
        "updated_at = excluded.updated_at",
        (
            champion_id,
            lore,
            blurb,
            json.dumps(tags, ensure_ascii=False),
            json.dumps(ally_tips, ensure_ascii=False),
            json.dumps(enemy_tips, ensure_ascii=False),
            json.dumps(abilities, ensure_ascii=False),
            updated_at,
        ),
    )


def upsert_champion_build(
    conn: sqlite3.Connection,
    champion_id: int,
    position: str,
    item_type: str,
    ordinal: int,
    *,
    item_id: int = 0,
    item_name: str = "",
    play: int = 0,
    win: int = 0,
    pick_rate: float = 0.0,
) -> None:
    conn.execute(
        "INSERT INTO champion_builds"
        "(champion_id, position, item_type, ordinal, item_id, item_name, play, win, pick_rate) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(champion_id, position, item_type, ordinal) DO UPDATE SET "
        "item_id = excluded.item_id, item_name = excluded.item_name, play = excluded.play, "
        "win = excluded.win, pick_rate = excluded.pick_rate",
        (champion_id, position, item_type, ordinal, item_id, item_name, play, win, pick_rate),
    )


def upsert_champion_rune(
    conn: sqlite3.Connection, champion_id: int, position: str, tree: str, slot: int, rune: str
) -> None:
    conn.execute(
        "INSERT INTO champion_runes(champion_id, position, tree, slot, rune) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(champion_id, position, tree, slot) DO UPDATE SET rune = excluded.rune",
        (champion_id, position, tree, slot, rune),
    )


def upsert_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    name: str,
    description: str,
    gold_total: int,
    builds_into: list[int],
    builds_from: list[int],
) -> None:
    conn.execute(
        "INSERT INTO items(item_id, name, description, gold_total, builds_into, builds_from) "
        "VALUES(?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(item_id) DO UPDATE SET name = excluded.name, "
        "description = excluded.description, gold_total = excluded.gold_total, "
        "builds_into = excluded.builds_into, builds_from = excluded.builds_from",
        (
            item_id,
            name,
            description,
            gold_total,
            json.dumps(builds_into),
            json.dumps(builds_from),
        ),
    )


def name_to_id(conn: sqlite3.Connection) -> dict[str, int]:
    """Case-insensitive champion display name -> champion_id."""
    rows = conn.execute("SELECT champion_id, name FROM champions").fetchall()
    return {str(row["name"]).lower(): int(row["champion_id"]) for row in rows}


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("champions", "champion_stats", "champion_roles", "matchups", "synergies",
              "analysis_done", "champion_reference", "champion_builds", "champion_runes",
              "items")
    result: dict[str, int] = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()  # noqa: S608
        result[table] = int(row["n"]) if row is not None else 0
    return result
