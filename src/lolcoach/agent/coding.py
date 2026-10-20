"""The tool-loop a coding agent runs, shared by the fixer and the reviewer.

A small, synchronous loop: ask the model for the next action, execute a
workspace tool, feed the result back, repeat until the model calls the *terminal*
tool (which carries the structured result) or the round budget runs out. The
model call goes through :class:`~lolcoach.agent.llm.LlmClient`, so — inside a
:func:`~lolcoach.observability.tracing.trace` block — every round is recorded in
Langfuse exactly like a chat turn.

There is no offline fallback: with no client, or if the model never submits, the
loop raises :class:`LlmUnavailableError` and the workflow step fails rather than
fabricating work.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from lolcoach.agent.llm import LlmClient, LlmMessage, LlmToolCall, LlmUnavailableError
from lolcoach.agent.workspace import Workspace, WorkspaceError
from lolcoach.config import Settings
from lolcoach.values import as_str

logger = logging.getLogger(__name__)


class AgentLoopError(LlmUnavailableError):
    """The coding agent could not produce a result (no client, or out of rounds)."""


def fn(
    name: str, description: str, properties: dict[str, object], required: Sequence[str]
) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        },
    }


_STRING = {"type": "string"}


def workspace_tools(*, writable: bool) -> list[dict[str, object]]:
    """The read/verify tools, plus ``write_file`` when the agent may edit."""
    tools = [
        fn("read_file", "Read a UTF-8 file from the repository.", {"path": _STRING}, ["path"]),
        fn(
            "list_files",
            "List repository files under a path prefix (empty = the whole repo).",
            {"prefix": _STRING},
            [],
        ),
        fn(
            "run_command",
            "Run a permitted read/verify command (tests, lint, type-check, git status).",
            {"command": _STRING},
            ["command"],
        ),
    ]
    if writable:
        tools.append(
            fn(
                "write_file",
                "Create or overwrite a file with new content. The only way to change the repo.",
                {"path": _STRING, "content": _STRING},
                ["path", "content"],
            )
        )
    return tools


def execute_tool(call: LlmToolCall, workspace: Workspace) -> dict[str, object]:
    """Run one non-terminal tool call, returning a JSON-safe result.

    A refused or failing tool is reported as ``{"error": ...}`` rather than
    raised, so the model can react to a bad path or a blocked command instead of
    the whole run dying on it.
    """
    args = call.arguments
    try:
        if call.name == "read_file":
            return {"content": workspace.read(as_str(args.get("path")))}
        if call.name == "list_files":
            return {"files": workspace.list_files(as_str(args.get("prefix")))}
        if call.name == "write_file":
            path = as_str(args.get("path"))
            workspace.write(path, as_str(args.get("content")))
            return {"ok": True, "path": path}
        if call.name == "run_command":
            return workspace.run(as_str(args.get("command"))).to_dict()
        return {"error": f"unknown tool {call.name!r}"}
    except WorkspaceError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the run
        logger.exception("Coding agent tool failed", extra={"data": {"tool": call.name}})
        return {"error": f"{type(exc).__name__}: {exc}"}


def run_coding_loop(
    client: LlmClient | None,
    settings: Settings,
    *,
    system: str,
    user: str,
    tools: list[dict[str, object]],
    terminal: str,
    workspace: Workspace,
    max_rounds: int = 12,
) -> dict[str, object]:
    """Loop until the model calls ``terminal``; return that call's arguments.

    ``tools`` must include ``terminal``. Every other call is executed against
    ``workspace``. Raises :class:`AgentLoopError` when no client is configured or
    the model never submits within ``max_rounds``.
    """
    if client is None:
        raise AgentLoopError("no LLM client is configured for the coding agent")

    messages: list[LlmMessage] = [LlmMessage("system", system), LlmMessage("user", user)]
    for round_index in range(max_rounds):
        try:
            reply = client.complete(messages, tools)
        except Exception as exc:  # noqa: BLE001 - any provider failure ends the loop
            logger.exception("Coding agent model call failed")
            raise AgentLoopError(f"model call failed: {type(exc).__name__}: {exc}") from exc
        if not reply.tool_calls:
            logger.warning(
                "Coding agent stopped without submitting",
                extra={"data": {"terminal": terminal, "round": round_index}},
            )
            raise AgentLoopError(f"the agent returned prose instead of calling {terminal!r}")
        call = reply.tool_calls[0]
        if call.name == terminal:
            return dict(call.arguments)
        result = execute_tool(call, workspace)
        messages.append(
            LlmMessage(
                "assistant",
                tool_calls=(LlmToolCall(id=call.id, name=call.name, arguments=call.arguments),),
            )
        )
        messages.append(
            LlmMessage(
                "tool",
                json.dumps(result, sort_keys=True, default=str),
                tool_call_id=call.id,
            )
        )
    raise AgentLoopError(f"the agent did not submit {terminal!r} within {max_rounds} rounds")


@dataclass(frozen=True, slots=True)
class LoopBudget:
    """How many rounds each phase may take (kept small and explicit)."""

    investigate: int = 8
    plan: int = 4
    implement: int = 16
    review: int = 10

    @classmethod
    def from_settings(cls, settings: Settings) -> LoopBudget:
        budget = settings.agent_loop_rounds
        return cls(
            investigate=budget,
            plan=max(2, budget // 2),
            implement=budget * 2,
            review=budget,
        )


def describe_issue(title: str, body: str, extra: str = "") -> str:
    """A stable prompt framing of an issue, shared by the coder and the reviewer."""
    parts = [f"Title: {title}", "", body.strip()]
    if extra:
        parts.extend(["", extra])
    return "\n".join(parts)
