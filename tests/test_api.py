"""Tests for the HTTP layer (routes wired to the workflow + execution store)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from lolcoach.agent.llm import LlmMessage, LlmReply, LlmToolCall
from lolcoach.config import get_settings
from lolcoach.server.app import create_app
from lolcoach.workflow.events import EXECUTION_STARTED, EXECUTION_SUCCEEDED


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast("list[object]", value)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LOL_COACH_EXECUTIONS_DB", str(tmp_path / "executions.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _evaluate(client: TestClient, payload: dict[str, object]) -> dict[str, object]:
    response = client.post("/api/draft/evaluate", json=payload)
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


def _chat(client: TestClient, payload: dict[str, object]) -> dict[str, object]:
    response = client.post("/api/agent/chat", json=payload)
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


class _FakeClient:
    """A scripted LlmClient: drives actions when tools are offered, else narrates."""

    provider = "fake"

    def __init__(
        self, actions: list[tuple[str | None, dict[str, object]]], narration: str
    ) -> None:
        self._actions = list(actions)
        self._narration = narration

    def complete(
        self, _messages: Sequence[LlmMessage], tools: Sequence[dict[str, object]]
    ) -> LlmReply:
        if tools:
            name, args = self._actions.pop(0) if self._actions else (None, {})
            if name is None:
                return LlmReply("done", ())
            return LlmReply("", (LlmToolCall(id=f"call-{name}", name=name, arguments=args),))
        return LlmReply(self._narration, ())


def _use_client(
    monkeypatch: pytest.MonkeyPatch,
    actions: list[tuple[str | None, dict[str, object]]],
    narration: str = "Take the counter-pick; the tools measured it.",
) -> None:
    """Make the app build a scripted client instead of the real one."""
    fake = _FakeClient(actions, narration)
    monkeypatch.setattr("lolcoach.server.app.build_client", lambda _settings: fake)


def test_champions_lists_the_roster(client: TestClient) -> None:
    response = client.get("/api/champions")
    assert response.status_code == 200
    champions = response.json()
    assert len(champions) > 100
    slugs = {c["slug"] for c in champions}
    assert "jinx" in slugs
    jinx = next(c for c in champions if c["slug"] == "jinx")
    assert jinx["positions"]


def test_champions_expose_the_ddragon_key(client: TestClient) -> None:
    champions = client.get("/api/champions").json()
    keys = {c["slug"]: c["key"] for c in champions}
    assert keys["jinx"] == "Jinx"
    assert all(keys.values())


def test_health_reports_the_data_dragon_version(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["patch"] == "14.20"
    assert body["ddragon"] == "14.20.1"


def test_evaluate_runs_workflow_and_records_execution(client: TestClient) -> None:
    body = _evaluate(client, {"allies": ["jinx"], "enemies": ["ahri", "vi"], "open_roles": ["mid"]})
    assert body["status"] == "succeeded"
    assert body["counter"] is not None or body["synergy"] is not None
    execution_id = str(body["execution_id"])

    listing = client.get("/api/executions").json()
    assert any(row["execution_id"] == execution_id for row in listing)
    assert listing[0]["status"] == "succeeded"

    detail = client.get(f"/api/executions/{execution_id}").json()
    assert detail["execution_id"] == execution_id
    events = detail["events"]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == EXECUTION_STARTED
    assert events[-1]["type"] == EXECUTION_SUCCEEDED


def test_invalid_draft_is_recorded_as_failed(client: TestClient) -> None:
    body = _evaluate(
        client, {"allies": ["jinx"], "ally_bans": ["jinx"], "open_roles": ["mid"]}
    )
    assert body["status"] == "failed"
    assert body["problems"]
    assert body["counter"] is None and body["synergy"] is None

    detail = client.get(f"/api/executions/{body['execution_id']}").json()
    assert detail["status"] == "failed"
    assert any(e["type"] == "STATE_FAILED" for e in detail["events"])


def test_unknown_execution_is_404(client: TestClient) -> None:
    assert client.get("/api/executions/nope").status_code == 404


def test_chat_narrates_offline_without_a_key(client: TestClient) -> None:
    body = _evaluate(client, {"allies": ["jinx"], "enemies": ["ahri"], "open_roles": ["mid"]})
    response = client.post("/api/chat", json={"execution_id": body["execution_id"]})
    assert response.status_code == 200
    answered = response.json()
    assert answered["mode"] == "offline"
    assert answered["reply"]


def test_agent_chat_without_a_key_is_503(client: TestClient) -> None:
    response = client.post("/api/agent/chat", json={"message": "jinx vs their zeri"})

    assert response.status_code == 503
    assert "LLM" in response.json()["detail"]


def test_agent_chat_uses_the_llm(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_client(
        monkeypatch,
        [
            ("set_board", {"allies": ["jinx"], "enemies": ["zeri"]}),
            ("suggest_pick", {}),
        ],
    )
    body = _chat(client, {"message": "jinx vs their zeri"})

    assert body["status"] == "succeeded"
    assert body["mode"] == "llm"
    board = _dict(body["board"])
    assert board["allies"] == ["jinx"]
    assert board["enemies"] == ["zeri"]
    names = [_dict(call)["name"] for call in _list(body["tool_calls"])]
    assert names[:2] == ["set_board", "suggest_pick"]
    assert body["reply"]
    assert body["counter"] is not None or body["synergy"] is not None

    detail = client.get(f"/api/executions/{body['execution_id']}").json()
    assert detail["workflow"] == "agent-chat"
    assert detail["status"] == "succeeded"
    assert detail["events"][0]["type"] == EXECUTION_STARTED


def test_agent_chat_fails_when_the_model_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BoomClient:
        provider = "boom"

        def complete(self, _messages: object, _tools: object) -> object:
            raise RuntimeError("upstream 500")

    monkeypatch.setattr("lolcoach.server.app.build_client", lambda _settings: _BoomClient())
    response = client.post("/api/agent/chat", json={"message": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["reply"] == ""
    assert "LLM call failed" in (body["error"] or "")


def test_agent_chat_uses_a_supplied_board(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_client(monkeypatch, [("suggest_pick", {})])
    body = _chat(
        client,
        {"message": "what now?", "board": {"allies": ["jinx"], "enemies": ["zeri"]}},
    )

    assert body["status"] == "succeeded"
    names = [_dict(call)["name"] for call in _list(body["tool_calls"])]
    assert names == ["suggest_pick"]


def test_agent_chat_reports_unresolved_names(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_client(monkeypatch, [("suggest_pick", {})])
    body = _chat(
        client,
        {
            "message": "go",
            "board": {"allies": ["jinx", "zzzznotachamp"], "enemies": ["zeri"]},
        },
    )

    assert "zzzznotachamp" in _list(body["unresolved"])
    assert _dict(body["board"])["allies"] == ["jinx"]
