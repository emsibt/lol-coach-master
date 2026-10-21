"""Tests for chat sessions: persistence, the question cap, and agent memory."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from lolcoach.agent.llm import LlmMessage, LlmReply, LlmToolCall
from lolcoach.config import get_settings
from lolcoach.server.app import create_app


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast("list[object]", value)


class _FakeClient:
    """A scripted LlmClient that also records the prompts it was handed."""

    provider = "fake"

    def __init__(self, actions: list[tuple[str | None, dict[str, object]]], narration: str) -> None:
        self._actions = list(actions)
        self._narration = narration
        self.seen: list[list[LlmMessage]] = []

    def complete(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[dict[str, object]],
        *,
        tool_choice: str | None = None,
    ) -> LlmReply:
        self.seen.append(list(messages))
        if tools:
            name, args = self._actions.pop(0) if self._actions else (None, {})
            if name is None:
                return LlmReply("done", ())
            return LlmReply("", (LlmToolCall(id=f"call-{name}", name=name, arguments=args),))
        return LlmReply(self._narration, ())


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LOL_COACH_EXECUTIONS_DB", str(tmp_path / "executions.db"))
    monkeypatch.setenv("LOL_COACH_CHAT_DB", str(tmp_path / "chat.db"))
    monkeypatch.setenv("LOL_COACH_MAX_QUESTIONS", "2")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _use_client(
    monkeypatch: pytest.MonkeyPatch,
    actions: list[tuple[str | None, dict[str, object]]],
    narration: str = "Take the counter-pick; the tools measured it.",
) -> _FakeClient:
    fake = _FakeClient(actions, narration)
    monkeypatch.setattr("lolcoach.server.app.build_client", lambda _settings: fake)
    return fake


def _new_session(client: TestClient, title: str | None = None) -> dict[str, object]:
    response = client.post("/api/sessions", json={"title": title})
    assert response.status_code == 201
    body: dict[str, object] = response.json()
    return body


def _ask(client: TestClient, session_id: str, message: str) -> int:
    response = client.post(
        "/api/agent/chat", json={"message": message, "session_id": session_id}
    )
    return response.status_code


def test_session_crud(client: TestClient) -> None:
    session = _new_session(client, "Fizz mid")
    session_id = str(session["session_id"])
    assert session["max_questions"] == 2
    assert session["remaining_questions"] == 2

    listing = client.get("/api/sessions").json()
    assert [row["session_id"] for row in listing] == [session_id]

    renamed = client.patch(f"/api/sessions/{session_id}", json={"title": "Ahri mid"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Ahri mid"

    assert client.get(f"/api/sessions/{session_id}").status_code == 200
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.patch("/api/sessions/nope", json={"title": "x"}).status_code == 404


def test_agent_chat_persists_turns_to_the_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = str(_new_session(client)["session_id"])
    _use_client(monkeypatch, [("set_board", {"allies": ["jinx"], "enemies": ["zeri"]})])

    body = _dict(
        client.post(
            "/api/agent/chat", json={"message": "jinx vs their zeri", "session_id": session_id}
        ).json()
    )
    assert body["status"] == "succeeded"
    assert body["session_id"] == session_id
    assert body["question_count"] == 1
    assert body["remaining_questions"] == 1

    detail = _dict(client.get(f"/api/sessions/{session_id}").json())
    messages = _list(detail["messages"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "jinx vs their zeri"
    assert _dict(messages[1])["execution_id"] == body["execution_id"]
    assert detail["question_count"] == 1
    assert detail["board"]["allies"] == ["jinx"]


def test_session_question_cap_is_enforced(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = str(_new_session(client)["session_id"])
    _use_client(monkeypatch, [(None, {})])

    assert _ask(client, session_id, "one") == 200
    assert _ask(client, session_id, "two") == 200

    blocked = client.post("/api/agent/chat", json={"message": "three", "session_id": session_id})
    assert blocked.status_code == 409
    assert "limit" in blocked.json()["detail"]


def test_agent_memory_tools_persist_and_are_reinjected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = str(_new_session(client)["session_id"])
    _use_client(monkeypatch, [("remember", {"note": "enemy is dive-heavy"})])

    first = _dict(
        client.post(
            "/api/agent/chat", json={"message": "who should I pick", "session_id": session_id}
        ).json()
    )
    assert first["status"] == "succeeded"
    detail = _dict(client.get(f"/api/sessions/{session_id}").json())
    assert detail["notes"] == ["enemy is dive-heavy"]

    # A later turn is handed the stored note back as session memory.
    fake = _use_client(monkeypatch, [("recall_history", {})])
    second = _dict(
        client.post(
            "/api/agent/chat", json={"message": "and now?", "session_id": session_id}
        ).json()
    )
    assert second["status"] == "succeeded"
    prompts = "\n".join(message.text or "" for call in fake.seen for message in call)
    assert "enemy is dive-heavy" in prompts


def test_unknown_session_is_404(client: TestClient) -> None:
    response = client.post("/api/agent/chat", json={"message": "hi", "session_id": "nope"})
    assert response.status_code == 404


def test_session_chat_without_a_key_is_503(client: TestClient) -> None:
    session_id = str(_new_session(client)["session_id"])
    response = client.post("/api/agent/chat", json={"message": "hi", "session_id": session_id})
    assert response.status_code == 503
    # The question was not consumed: no LLM means no turn is recorded.
    assert _dict(client.get(f"/api/sessions/{session_id}").json())["question_count"] == 0
