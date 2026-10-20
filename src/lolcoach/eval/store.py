"""Persistent history of evaluation runs (``data/eval.db``).

The execution store already keeps each scenario's *execution* so a failure opens
in the Trace Inspector. It does not keep the *suite verdict* — the per-scenario
pass/fail roll-up — which is what you want when asking "how has the eval been
doing?". This store keeps exactly that: one row per suite run, with the full
report as JSON, so the Evaluation view can list runs over time and open any one.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lolcoach.values import as_int, as_str, opt_dict, opt_list

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    total       INTEGER NOT NULL DEFAULT 0,
    passed      INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    report      TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_started ON eval_runs (started_at DESC);
"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EvalRunSummary:
    run_id: str
    kind: str
    provider: str
    model: str
    started_at: str
    finished_at: str | None
    total: int
    passed: int
    failed: int

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> EvalRunSummary:
        return cls(
            run_id=as_str(row["run_id"]),
            kind=as_str(row["kind"]),
            provider=as_str(row["provider"]),
            model=as_str(row["model"]),
            started_at=as_str(row["started_at"]),
            finished_at=as_str(row["finished_at"]) or None,
            total=as_int(row["total"]),
            passed=as_int(row["passed"]),
            failed=as_int(row["failed"]),
        )


@dataclass(frozen=True, slots=True)
class EvalRunRecord(EvalRunSummary):
    report: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> EvalRunRecord:
        summary = EvalRunSummary.from_row(row)
        try:
            report = opt_dict(json.loads(as_str(row["report"], "{}")))
        except json.JSONDecodeError:  # pragma: no cover - we always write valid JSON
            report = {}
        return cls(
            run_id=summary.run_id,
            kind=summary.kind,
            provider=summary.provider,
            model=summary.model,
            started_at=summary.started_at,
            finished_at=summary.finished_at,
            total=summary.total,
            passed=summary.passed,
            failed=summary.failed,
            report=report,
        )


def counts_of(report: dict[str, object]) -> tuple[int, int, int]:
    """``(total, passed, failed)`` from a report, tolerating a partial report."""
    scenarios = opt_list(report.get("scenarios"))
    total = as_int(report.get("total")) or len(scenarios)
    passed = as_int(report.get("passed")) or sum(
        1 for item in scenarios if bool(opt_dict(item).get("passed"))
    )
    failed = as_int(report.get("failed")) or (total - passed)
    return total, passed, failed


class EvalRunStore:
    """Thin wrapper over a SQLite connection holding evaluation-run history."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, path: Path) -> EvalRunStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(DDL)
        conn.commit()
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def record(
        self,
        *,
        kind: str,
        provider: str,
        model: str,
        report: dict[str, object],
        started_at: str | None = None,
    ) -> str:
        """Persist one suite report; returns its run id."""
        total, passed, failed = counts_of(report)
        run_id = uuid4().hex
        self._conn.execute(
            "INSERT INTO eval_runs"
            " (run_id, kind, provider, model, started_at, finished_at,"
            "  total, passed, failed, report)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                kind,
                provider,
                model,
                started_at or _utc_now_iso(),
                _utc_now_iso(),
                total,
                passed,
                failed,
                json.dumps(report, default=str),
            ),
        )
        self._conn.commit()
        logger.info(
            "Evaluation run recorded",
            extra={"data": {"run_id": run_id, "kind": kind, "passed": passed, "total": total}},
        )
        return run_id

    def list_runs(self, limit: int = 50) -> list[EvalRunSummary]:
        rows = self._conn.execute(
            "SELECT run_id, kind, provider, model, started_at, finished_at, total, passed, failed"
            " FROM eval_runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [EvalRunSummary.from_row(row) for row in rows]

    def get_run(self, run_id: str) -> EvalRunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM eval_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return EvalRunRecord.from_row(row) if row is not None else None
