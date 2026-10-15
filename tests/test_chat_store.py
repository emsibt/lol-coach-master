"""Tests for the chat-session store (``data/chat.db``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach.chat.memory import SessionMemory, call_memory_tool
from lolcoach.chat.store import ChatStore, SessionLimitReached


@pytest.fixture()
def store(tmp_path: Path) -> ChatStore:
    chat = ChatStore.connect(tmp_path / "chat.db")
    yield chat
    chat.close()


def test_session_roundtrip_and_auto_title(store: ChatStore) -> None:
    session = store.create_session(max_questions=5)
    assert session.title == "New chat"
    assert session.question_count == 0 and session.remaining_questions == 5

    store.append_question(session.session_id, "jinx vs their zeri")
    store.append_message(
        session.session_id, "assistant", "Take the counter-pick.", meta={"mode": "llm"}
    )

    refreshed = store.get_session(session.session_id)
    assert refreshed is not None
    assert refreshed.title == "jinx vs their zeri"  # auto-titled from the first question
    assert refreshed.question_count == 1
    assert refreshed.message_count == 2
    assert refreshed.remaining_questions == 4

    messages = store.messages(session.session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].meta["mode"] == "llm"


def test_question_cap_is_enforced(store: ChatStore) -> None:
    session = store.create_session(max_questions=2)
    store.append_question(session.session_id, "one")
    store.append_question(session.session_id, "two")
    with pytest.raises(SessionLimitReached):
        store.append_question(session.session_id, "three")
    # Assistant messages do not consume slots.
    store.append_message(session.session_id, "assistant", "ok")
    assert store.get_session(session.session_id).question_count == 2  # type: ignore[union-attr]


def test_notes_and_lifecycle(store: ChatStore) -> None:
    session = store.create_session()
    store.add_note(session.session_id, "enemy is dive-heavy")
    store.add_note(session.session_id, "   ")
    assert store.notes(session.session_id) == ["enemy is dive-heavy"]

    renamed = store.rename_session(session.session_id, "Fizz mid")
    assert renamed is not None and renamed.title == "Fizz mid"

    assert len(store.list_sessions()) == 1
    assert store.delete_session(session.session_id) is True
    assert store.get_session(session.session_id) is None
    assert store.delete_session(session.session_id) is False
    # Cascade removed the messages and notes.
    assert store.counts() == {"sessions": 0, "messages": 0, "notes": 0}


def test_session_memory_recall_and_tools(store: ChatStore) -> None:
    session = store.create_session()
    store.append_question(session.session_id, "who counters zeri")
    store.append_message(session.session_id, "assistant", "varus")
    store.add_note(session.session_id, "playing adc")

    memory = SessionMemory.from_session(
        store.get_session(session.session_id),  # type: ignore[arg-type]
        store.messages(session.session_id),
        store.notes(session.session_id),
    )
    assert memory.turns == (("user", "who counters zeri"), ("assistant", "varus"))
    assert memory.notes == ("playing adc",)
    assert memory.recall(1) == [{"role": "assistant", "text": "varus"}]

    recalled = call_memory_tool("recall_history", {}, memory)
    assert recalled is not None
    assert recalled.payload["notes"] == ["playing adc"]
    assert recalled.payload["remaining_questions"] == 4

    remembered = call_memory_tool("remember", {"note": "enemy first-picks zeri"}, memory)
    assert remembered is not None
    assert remembered.recorded_note == "enemy first-picks zeri"

    assert call_memory_tool("set_board", {}, memory) is None  # not a memory tool
    empty = call_memory_tool("remember", {"note": "  "}, memory)
    assert empty is not None and empty.payload["ok"] is False
