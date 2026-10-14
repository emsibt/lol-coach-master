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


def name_to_id(conn: sqlite3.Connection) -> dict[str, int]:
    """Case-insensitive champion display name -> champion_id."""
    rows = conn.execute("SELECT champion_id, name FROM champions").fetchall()
    return {str(row["name"]).lower(): int(row["champion_id"]) for row in rows}


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("champions", "champion_stats", "champion_roles", "matchups", "synergies",
              "analysis_done")
    result: dict[str, int] = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()  # noqa: S608
        result[table] = int(row["n"]) if row is not None else 0
    return result
