"""SQLite-backed execution history for the workflow executor.

The executor writes through on every event, so a crash mid-run still leaves a
faithful partial history — which is the point of building the executor in-house.
This is a *separate* database (``data/executions.db``) from the replaceable OP.GG
snapshot.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import cast

from lolcoach.workflow.ddl import DDL
from lolcoach.workflow.events import Event

logger = logging.getLogger(__name__)


def _dump(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str, ensure_ascii=True)


def _load(text: str | None) -> object | None:
    if text is None:
        return None
    return cast(object, json.loads(text))


class ExecutionStore:
    """Thin wrapper over a SQLite connection holding executions and their events."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, path: Path) -> ExecutionStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        store = cls(conn)
        store.init_db()
        return store

    def init_db(self) -> None:
        self._conn.executescript(DDL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def create_execution(
        self,
        execution_id: str,
        workflow: str,
        version: str,
        started_at: str,
        input: object,
    ) -> None:
        self._conn.execute(
            "INSERT INTO executions (execution_id, workflow, version, status, started_at, input)"
            " VALUES (?, ?, ?, 'running', ?, ?)",
            (execution_id, workflow, version, started_at, _dump(input)),
        )
        self._conn.commit()

    def append(self, event: Event) -> None:
        self._conn.execute(
            "INSERT INTO execution_events"
            " (execution_id, seq, type, state_name, timestamp, input, output, error, traceback,"
            " duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.execution_id,
                event.seq,
                event.type,
                event.state_name,
                event.timestamp,
                _dump(event.input),
                _dump(event.output),
                event.error,
                event.traceback,
                event.duration_ms,
            ),
        )
        self._conn.commit()

    def finish(
        self,
        execution_id: str,
        status: str,
        finished_at: str,
        output: object,
        error: str | None,
    ) -> None:
        self._conn.execute(
            "UPDATE executions SET status = ?, finished_at = ?, output = ?, error = ?"
            " WHERE execution_id = ?",
            (status, finished_at, _dump(output), error, execution_id),
        )
        self._conn.commit()

    def list_executions(self, limit: int = 50) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT execution_id, workflow, version, status, started_at, finished_at, error"
            " FROM executions ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_execution(self, execution_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["input"] = _load(row["input"])
        record["output"] = _load(row["output"])
        return record

    def events(self, execution_id: str) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT seq, type, state_name, timestamp, input, output, error, traceback, duration_ms"
            " FROM execution_events WHERE execution_id = ? ORDER BY seq ASC",
            (execution_id,),
        ).fetchall()
        out: list[dict[str, object]] = []
        for row in rows:
            event = dict(row)
            event["input"] = _load(row["input"])
            event["output"] = _load(row["output"])
            out.append(event)
        return out

    def counts(self) -> dict[str, int]:
        executions = self._conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        events = self._conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0]
        return {"executions": int(executions), "events": int(events)}
