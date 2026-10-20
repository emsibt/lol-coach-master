"""Tests for the shared coding tool-loop the fixer and reviewer run.

A scripted ``LlmClient`` drives the loop, so the terminal-tool contract, the
workspace tool dispatch, and the failure modes (no client, prose instead of a
tool call, out of rounds) are all exercised without a model.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from lolcoach.agent.coding import (
    AgentLoopError,
    LoopBudget,
    describe_issue,
    execute_tool,
    run_coding_loop,
    workspace_tools,
)
from lolcoach.agent.fixer import LlmCoder, ScriptedCoder
from lolcoach.agent.llm import LlmMessage, LlmReply, LlmToolCall
from lolcoach.agent.workspace import FakeWorkspace
from lolcoach.config import get_settings
from lolcoach.github.types import Issue


class _ScriptedClient:
    """Returns canned replies in order; records the messages it was shown."""

    provider = "scripted"

    def __init__(self, replies: list[LlmReply]) -> None:
        self._replies = list(replies)
        self.seen: list[LlmMessage] = []

    def complete(
        self, messages: Sequence[LlmMessage], tools: Sequence[dict[str, object]]
    ) -> LlmReply:
        self.seen = list(messages)
        return self._replies.pop(0)


def _reply(*calls: LlmToolCall, content: str = "") -> LlmReply:
    return LlmReply(content, calls)


def _call(name: str, **arguments: object) -> LlmToolCall:
    return LlmToolCall(id=f"call-{name}", name=name, arguments=dict(arguments))


def test_workspace_tools_hide_write_file_when_read_only() -> None:
    read_only = {_name(spec) for spec in workspace_tools(writable=False)}
    writable = {_name(spec) for spec in workspace_tools(writable=True)}

    assert "write_file" not in read_only
    assert "write_file" in writable
    assert {"read_file", "list_files", "run_command"} <= read_only


def test_execute_tool_dispatches_to_the_workspace() -> None:
    workspace = FakeWorkspace()
    workspace.seed({"a.py": "hi"})

    assert execute_tool(_call("read_file", path="a.py"), workspace) == {"content": "hi"}
    assert execute_tool(_call("list_files"), workspace) == {"files": ["a.py"]}
    assert execute_tool(_call("write_file", path="b.py", content="x"), workspace) == {
        "ok": True,
        "path": "b.py",
    }
    assert workspace.files["b.py"] == "x"


def test_execute_tool_reports_a_refusal_as_an_error() -> None:
    result = execute_tool(_call("read_file", path=".env"), FakeWorkspace())
    assert "error" in result


def test_execute_tool_reports_an_unknown_tool() -> None:
    result = execute_tool(_call("explode"), FakeWorkspace())
    assert "unknown tool" in str(result["error"])


def test_the_loop_returns_the_terminal_call_arguments() -> None:
    workspace = FakeWorkspace()
    workspace.seed({"a.py": "hi"})
    client = _ScriptedClient(
        [
            _reply(_call("read_file", path="a.py")),
            _reply(_call("submit_investigation", summary="found it", confidence=0.9)),
        ]
    )

    args = run_coding_loop(
        client,
        get_settings(),
        system="sys",
        user="user",
        tools=[*workspace_tools(writable=False), _terminal("submit_investigation")],
        terminal="submit_investigation",
        workspace=workspace,
    )

    assert args == {"summary": "found it", "confidence": 0.9}
    assert workspace.reads == ["a.py"]
    # The tool result was fed back before the terminal call.
    assert any(message.role == "tool" for message in client.seen)


def test_the_loop_raises_without_a_client() -> None:
    with pytest.raises(AgentLoopError, match="no LLM client"):
        run_coding_loop(
            None,
            get_settings(),
            system="s",
            user="u",
            tools=[_terminal("done")],
            terminal="done",
            workspace=FakeWorkspace(),
        )


def test_the_loop_raises_when_the_model_writes_prose() -> None:
    client = _ScriptedClient([_reply(content="I think it's fine")])
    with pytest.raises(AgentLoopError, match="prose"):
        run_coding_loop(
            client,
            get_settings(),
            system="s",
            user="u",
            tools=[_terminal("done")],
            terminal="done",
            workspace=FakeWorkspace(),
        )


def test_the_loop_raises_when_it_runs_out_of_rounds() -> None:
    workspace = FakeWorkspace()
    client = _ScriptedClient([_reply(_call("list_files"))] * 3)
    with pytest.raises(AgentLoopError, match="did not submit"):
        run_coding_loop(
            client,
            get_settings(),
            system="s",
            user="u",
            tools=[*workspace_tools(writable=False), _terminal("done")],
            terminal="done",
            workspace=workspace,
            max_rounds=3,
        )


def test_the_loop_wraps_a_model_failure() -> None:
    class _Boom:
        provider = "boom"

        def complete(
            self, messages: Sequence[LlmMessage], tools: Sequence[dict[str, object]]
        ) -> LlmReply:
            raise RuntimeError("provider down")

    with pytest.raises(AgentLoopError, match="model call failed"):
        run_coding_loop(
            _Boom(),
            get_settings(),
            system="s",
            user="u",
            tools=[_terminal("done")],
            terminal="done",
            workspace=FakeWorkspace(),
        )


def test_loop_budget_scales_with_the_settings_rounds() -> None:
    settings = get_settings()
    budget = LoopBudget.from_settings(settings)
    assert budget.investigate == settings.agent_loop_rounds
    assert budget.review == settings.agent_loop_rounds
    assert budget.implement == settings.agent_loop_rounds * 2
    assert budget.plan == max(2, settings.agent_loop_rounds // 2)


def test_describe_issue_includes_the_title_and_extra() -> None:
    text = describe_issue("title", "body", "extra")
    assert "Title: title" in text
    assert "body" in text
    assert "extra" in text


def test_llm_coder_files_changed_come_from_the_workspace() -> None:
    # The model claims to have changed "claimed.py" but only writes "real.py";
    # the implementation must report what is actually on disk.
    client = _ScriptedClient(
        [
            _reply(_call("write_file", path="real.py", content="new")),
            _reply(_call("submit_implementation", summary="did it", notes=["note"])),
        ]
    )
    coder = LlmCoder(client, get_settings())
    implementation = coder.implement(
        Issue(number=1, title="t"), plan=ScriptedCoder().plan_result, workspace=FakeWorkspace()
    )

    assert implementation.files_changed == ("real.py",)
    assert implementation.summary == "did it"
    assert implementation.notes == ("note",)


def _name(spec: dict[str, object]) -> str:
    function = spec["function"]
    assert isinstance(function, dict)
    return str(function["name"])


def _terminal(name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {"name": name, "description": "", "parameters": {}},
    }
