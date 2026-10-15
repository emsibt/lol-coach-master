"""SQLite-backed store for chat sessions (``data/chat.db``).

Sessions are mutable product state, so unlike the write-once execution history
this store supports create/rename/delete. The one invariant it enforces is the
**per-session question cap**: :meth:`ChatStore.append_question` raises
:class:`SessionLimitReached` rather than accepting a question past the limit, so
the cap holds no matter which caller reaches it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from lolcoach.chat.ddl import DDL

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUESTIONS = 5
DEFAULT_TITLE = "New chat"
_TITLE_MAX = 60


class SessionLimitReached(RuntimeError):
    """Raised when a session already holds its maximum number of questions."""


@dataclass(frozen=True, slots=True)
class Message:
    seq: int
    role: str
    text: str
    created_at: str
    meta: dict[str, object]


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    title: str
    board: dict[str, object]
    max_questions: int
    created_at: str
    updated_at: str
    question_count: int
    message_count: int

    @property
    def remaining_questions(self) -> int:
        return max(0, self.max_questions - self.question_count)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dump(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str, ensure_ascii=True)


def _load(text: str | None) -> object | None:
    if text is None:
        return None
    try:
        return cast(object, json.loads(text))
    except json.JSONDecodeError:
        return None


def _auto_title(text: str) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return DEFAULT_TITLE
    return collapsed[:_TITLE_MAX] + ("…" if len(collapsed) > _TITLE_MAX else "")


class ChatStore:
    """Thin wrapper over a SQLite connection holding sessions and their messages."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, path: Path) -> ChatStore:
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

    # -- sessions -----------------------------------------------------------

    def create_session(
        self,
        *,
        title: str | None = None,
        board: object | None = None,
        max_questions: int = DEFAULT_MAX_QUESTIONS,
    ) -> Session:
        session_id = uuid4().hex
        now = _utc_now_iso()
        self._conn.execute(
            "INSERT INTO sessions (session_id, title, board, max_questions, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                (title.strip() if title and title.strip() else DEFAULT_TITLE),
                _dump(board if isinstance(board, dict) else {}),
                max(1, max_questions),
                now,
                now,
            ),
        )
        self._conn.commit()
        created = self.get_session(session_id)
        assert created is not None  # just inserted
        logger.info("Chat session created", extra={"data": {"session_id": session_id}})
        return created

    def get_session(self, session_id: str) -> Session | None:
        row = self._conn.execute(
            "SELECT session_id, title, board, max_questions, created_at, updated_at,"
            " (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id)"
            "   AS message_count,"
            " (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id"
            "   AND m.role = 'user') AS question_count"
            " FROM sessions s WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return _session_from_row(row) if row is not None else None

    def list_sessions(self, limit: int = 100) -> list[Session]:
        rows = self._conn.execute(
            "SELECT session_id, title, board, max_questions, created_at, updated_at,"
            " (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id)"
            "   AS message_count,"
            " (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id"
            "   AND m.role = 'user') AS question_count"
            " FROM sessions s ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_session_from_row(row) for row in rows]

    def rename_session(self, session_id: str, title: str) -> Session | None:
        cleaned = title.strip()
        if not cleaned:
            return self.get_session(session_id)
        self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
            (cleaned, _utc_now_iso(), session_id),
        )
        self._conn.commit()
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self._conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Chat session deleted", extra={"data": {"session_id": session_id}})
        return deleted

    def touch_session(self, session_id: str, *, board: object | None = None) -> None:
        if board is None:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (_utc_now_iso(), session_id),
            )
        else:
            self._conn.execute(
                "UPDATE sessions SET board = ?, updated_at = ? WHERE session_id = ?",
                (_dump(board), _utc_now_iso(), session_id),
            )
        self._conn.commit()

    # -- messages -----------------------------------------------------------

    def append_message(
        self,
        session_id: str,
        role: str,
        text: str,
        *,
        meta: dict[str, object] | None = None,
    ) -> Message:
        """Append a message, assigning the next ``seq``. Does not enforce the cap."""
        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM session_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        seq = int(seq_row["next"])
        created_at = _utc_now_iso()
        self._conn.execute(
            "INSERT INTO session_messages (session_id, seq, role, text, meta, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, seq, role, text, _dump(meta), created_at),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (created_at, session_id),
        )
        self._conn.commit()
        return Message(seq=seq, role=role, text=text, created_at=created_at, meta=dict(meta or {}))

    def append_question(
        self,
        session_id: str,
        text: str,
        *,
        meta: dict[str, object] | None = None,
    ) -> Message:
        """Append a user question, enforcing the per-session cap atomically.

        Raises :class:`SessionLimitReached` when the session is full. On the first
        question the session is auto-titled from the question text.
        """
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        if session.question_count >= session.max_questions:
            raise SessionLimitReached(
                f"session {session_id} reached its limit of {session.max_questions} questions"
            )
        message = self.append_message(session_id, "user", text, meta=meta)
        if session.question_count == 0 and session.title == DEFAULT_TITLE:
            self._set_title_if_default(session_id, _auto_title(text))
        return message

    def _set_title_if_default(self, session_id: str, title: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET title = ? WHERE session_id = ? AND title = ?",
            (title, session_id, DEFAULT_TITLE),
        )
        self._conn.commit()

    def messages(self, session_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT seq, role, text, meta, created_at FROM session_messages"
            " WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
        out: list[Message] = []
        for row in rows:
            meta = _load(row["meta"])
            out.append(
                Message(
                    seq=int(row["seq"]),
                    role=str(row["role"]),
                    text=str(row["text"]),
                    created_at=str(row["created_at"]),
                    meta=cast("dict[str, object]", meta) if isinstance(meta, dict) else {},
                )
            )
        return out

    # -- memory notes -------------------------------------------------------

    def add_note(self, session_id: str, note: str) -> None:
        cleaned = note.strip()
        if not cleaned:
            return
        self._conn.execute(
            "INSERT INTO session_notes (session_id, note, created_at) VALUES (?, ?, ?)",
            (session_id, cleaned, _utc_now_iso()),
        )
        self._conn.commit()

    def notes(self, session_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT note FROM session_notes WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [str(row["note"]) for row in rows]

    def counts(self) -> dict[str, int]:
        sessions = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = self._conn.execute("SELECT COUNT(*) FROM session_messages").fetchone()[0]
        notes = self._conn.execute("SELECT COUNT(*) FROM session_notes").fetchone()[0]
        return {"sessions": int(sessions), "messages": int(messages), "notes": int(notes)}


def _session_from_row(row: sqlite3.Row) -> Session:
    board = _load(row["board"])
    return Session(
        session_id=str(row["session_id"]),
        title=str(row["title"]),
        board=cast("dict[str, object]", board) if isinstance(board, dict) else {},
        max_questions=int(row["max_questions"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        question_count=int(row["question_count"]),
        message_count=int(row["message_count"]),
    )
