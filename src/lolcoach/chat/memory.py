"""The agent's session memory: the transcript it can recall, and notes it records.

Two things make up a session's memory:

* the **transcript** — every prior user/assistant turn, replayed to the agent as
  conversation context, and reachable on demand through ``recall_history``;
* the **notes** — short facts the agent chose to keep (``remember``), which
  outlive the raw turn they came from and are re-injected on every turn.

:class:`SessionMemory` is the read model the server hands to a run.
:func:`call_memory_tool` implements the memory tools themselves in a pure,
store-free way, so the agent workflow stays independent of the database.
"""

from __future__ import annotations

from dataclasses import dataclass

from lolcoach.chat.store import Message, Session
from lolcoach.values import as_int, as_str

MEMORY_TOOL_NAMES = frozenset({"remember", "recall_history"})

_DEFAULT_RECALL_LIMIT = 10


@dataclass(frozen=True, slots=True)
class SessionMemory:
    """What the agent remembers for one session, as of this turn."""

    session_id: str = ""
    turns: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    question_count: int = 0
    max_questions: int = 0

    @property
    def remaining_questions(self) -> int:
        return max(0, self.max_questions - self.question_count)

    @classmethod
    def from_session(
        cls,
        session: Session,
        messages: list[Message],
        notes: list[str],
    ) -> SessionMemory:
        turns = tuple(
            (message.role, message.text)
            for message in messages
            if message.role in ("user", "assistant") and message.text.strip()
        )
        return cls(
            session_id=session.session_id,
            turns=turns,
            notes=tuple(notes),
            question_count=session.question_count,
            max_questions=session.max_questions,
        )

    def recall(self, limit: int | None) -> list[dict[str, str]]:
        """The most recent ``limit`` turns (all when ``limit`` is unset or <= 0)."""
        turns = self.turns
        if limit is not None and limit > 0:
            turns = turns[-limit:]
        return [{"role": role, "text": text} for role, text in turns]


def memory_tool_specs() -> list[dict[str, object]]:
    """Schemas for the memory tools, in OpenAI function format."""

    def fn(name: str, description: str, parameters: dict[str, object]) -> dict[str, object]:
        return {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": parameters},
        }

    return [
        fn(
            "recall_history",
            "Return this session's earlier conversation (and any notes you have kept). "
            "Use it to remember what the user already asked before answering.",
            {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many recent turns to return (default: all).",
                    }
                },
            },
        ),
        fn(
            "remember",
            "Keep a short note for the rest of this session (e.g. a board detail or a "
            "constraint the user stated). Notes are re-read on every later turn.",
            {
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
        ),
    ]


@dataclass(frozen=True, slots=True)
class MemoryCall:
    """The result of a memory tool: its payload, plus any note it recorded."""

    payload: dict[str, object]
    recorded_note: str = ""


def call_memory_tool(
    name: str,
    arguments: dict[str, object],
    memory: SessionMemory,
    *,
    existing_notes: list[str] | None = None,
) -> MemoryCall | None:
    """Run a memory tool. Returns ``None`` when ``name`` is not a memory tool."""
    if name not in MEMORY_TOOL_NAMES:
        return None
    if name == "recall_history":
        limit = as_int(arguments.get("limit"), 0)
        notes = list(existing_notes) if existing_notes else list(memory.notes)
        return MemoryCall(
            payload={
                "turns": memory.recall(limit if limit > 0 else None),
                "notes": notes,
                "question_count": memory.question_count,
                "remaining_questions": memory.remaining_questions,
            }
        )

    note = as_str(arguments.get("note")).strip()
    if not note:
        return MemoryCall(payload={"error": "remember requires a non-empty note", "ok": False})
    total = len(existing_notes) if existing_notes is not None else len(memory.notes)
    return MemoryCall(
        payload={"note": note, "ok": True, "notes_total": total + 1},
        recorded_note=note,
    )
