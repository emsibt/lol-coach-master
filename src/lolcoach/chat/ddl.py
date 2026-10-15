"""DDL for the chat-session database (``data/chat.db``).

Kept separate from ``data/executions.db`` (immutable audit trail) and
``data/snapshot.db`` (replaceable OP.GG stats): sessions are mutable product
state with their own lifecycle, so "delete a conversation" never touches the
trace history.
"""

from __future__ import annotations

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    board         TEXT,
    max_questions INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    role         TEXT NOT NULL,
    text         TEXT NOT NULL,
    meta         TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (session_id, seq)
);

CREATE TABLE IF NOT EXISTS session_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    note         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON session_messages (session_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_updated
    ON sessions (updated_at DESC);
"""
