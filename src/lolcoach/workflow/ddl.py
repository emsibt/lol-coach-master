"""DDL for the execution-history database (``data/executions.db``)."""

from __future__ import annotations

DDL = """
CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    workflow     TEXT NOT NULL,
    version      TEXT NOT NULL,
    status       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    input        TEXT,
    output       TEXT,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS execution_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    type         TEXT NOT NULL,
    state_name   TEXT,
    timestamp    TEXT NOT NULL,
    input        TEXT,
    output       TEXT,
    error        TEXT,
    traceback    TEXT,
    duration_ms  INTEGER,
    UNIQUE (execution_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_execution
    ON execution_events (execution_id, seq);
CREATE INDEX IF NOT EXISTS idx_executions_started
    ON executions (started_at DESC);
"""
