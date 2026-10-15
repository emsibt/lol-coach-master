"""The agent's decision step: which tool to call next, or stop.

This is the *only* place the LLM is consulted, and it is consulted about
orchestration — not about League. It runs a tool-loop over the deterministic
tools and emits a JSON-safe action dict, which the agent workflow persists and
the Trace Inspector renders.

There is no offline planner: if no client is configured or the model call fails,
:func:`choose_action` raises and the Think step — and the execution — fails. The
agent never fabricates an answer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from lolcoach.agent.llm import (
    ImagePart,
    LlmClient,
    LlmMessage,
    LlmToolCall,
    LlmUnavailableError,
)
from lolcoach.agent.tools import Board, tool_specs
from lolcoach.config import Settings
from lolcoach.values import as_str, opt_dict, opt_list

logger = logging.getLogger(__name__)

_AGENT_SYSTEM = """\
You are the orchestration layer of LoL Coach, a drafting assistant. You do NOT
decide picks and you do NOT know League matchups from memory. Your only jobs:

1. Adapter: turn the user's fuzzy wording (shorthand, typos, prose, or an image
   of a draft) into tool calls. Never invent a champion's stats.
2. Orchestration: call the tools the question needs, in whatever order, then stop.
3. Coach voice: when you have enough from the tools, stop calling tools and give
   a short, direct answer.

Hard rules:
- If the user names champions, call `set_board` first.
- If the user asks what to pick, call `suggest_pick`. It is the ONLY source of a
  pick recommendation - never state a pick it did not return.
- If the user asks how two champions interact, call `explain_matchup`.
- If the user asks for a champion's tips, abilities, or lore, call
  `get_champion_tips`. If they ask what to build (items, runes, skill order), call
  `get_champion_build`. Both are reference material, not picks.
- If the user asks about their own or someone else's rank, LP, or record, call
  `get_summoner_profile` with their Riot ID ("Name#TAG"). Report rank, LP, and
  win/loss only - it is never a reason to recommend a champion.
- If the user asks how to lane a specific matchup, call `get_lane_matchup_guide`
  for that pair and role. Its tip is evidence for the matchup they named.
- Every champion, number, and matchup in your answer must come from a tool
  result. If the tools are silent on something, say so.
- When you cite a tip or a build, do not name another champion: the reference
  text mentions other champions, which does not make them part of this draft.
- Never mention the engine, the knowledge graph, or "the model".
"""

@dataclass(frozen=True, slots=True)
class ToolStep:
    """One completed tool call: the request, and its result.

    ``sources`` is deliberately *not* part of ``payload``: it is the set of
    champion slugs the narrator may ground on for this step, and putting it in
    the payload would ship it to the model (which might then echo it). It travels
    with the step instead, through the workflow's execution data.
    """

    call_id: str
    name: str
    arguments: dict[str, object]
    ok: bool
    payload: dict[str, object]
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentContext:
    question: str
    board: Board
    image: ImagePart | None = None
    history: tuple[ToolStep, ...] = ()
    turns: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    memory: bool = False
    reference_tools: bool = False
    live_tools: bool = False


def step_to_dict(step: ToolStep) -> dict[str, object]:
    return {
        "call_id": step.call_id,
        "name": step.name,
        "arguments": step.arguments,
        "ok": step.ok,
        "payload": step.payload,
        "sources": list(step.sources),
    }


def step_from_dict(value: object) -> ToolStep:
    raw = opt_dict(value)
    return ToolStep(
        call_id=as_str(raw.get("call_id")),
        name=as_str(raw.get("name")),
        arguments=opt_dict(raw.get("arguments")),
        ok=bool(raw.get("ok")),
        payload=opt_dict(raw.get("payload")),
        sources=tuple(
            source for source in (as_str(item) for item in opt_list(raw.get("sources"))) if source
        ),
    )


def choose_action(
    context: AgentContext,
    *,
    client: LlmClient | None,
    settings: Settings,
) -> dict[str, object]:
    """Return the next action as a JSON-safe dict: a tool call or a final answer.

    Raises :class:`LlmUnavailableError` when no client is configured or the model
    call fails, so the Think step fails rather than inventing an action.
    """
    if client is None:
        raise LlmUnavailableError("no LLM client is configured for the agent")
    try:
        return _llm_action(context, client, settings)
    except LlmUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - any provider failure fails the Think step
        logger.exception("LLM action failed at the Think step")
        raise LlmUnavailableError(f"LLM call failed: {type(exc).__name__}: {exc}") from exc


def _llm_action(context: AgentContext, client: LlmClient, settings: Settings) -> dict[str, object]:
    image = context.image if settings.enable_vision else None
    user_text = context.question.strip() or "(see the attached draft image)"
    messages: list[LlmMessage] = [
        LlmMessage("system", _AGENT_SYSTEM),
        LlmMessage(
            "system",
            f"Current draft board (champion slugs): "
            f"{json.dumps(context.board, sort_keys=True)}",
        ),
    ]
    if context.memory:
        remembered = "\n".join(f"- {note}" for note in context.notes) or "(none yet)"
        messages.append(
            LlmMessage(
                "system",
                "Session memory. Notes you have kept this session:\n"
                f"{remembered}\n"
                "You can call `recall_history` to re-read earlier turns, and "
                "`remember` to keep a new note for later turns in this session.",
            )
        )
    for role, text in context.turns:
        if role in ("user", "assistant") and text.strip():
            messages.append(LlmMessage(role, text))
    messages.append(LlmMessage("user", user_text, image=image))
    for step in context.history:
        messages.append(
            LlmMessage(
                "assistant",
                tool_calls=(
                    LlmToolCall(id=step.call_id, name=step.name, arguments=step.arguments),
                ),
            )
        )
        messages.append(
            LlmMessage("tool", json.dumps(step.payload, sort_keys=True), tool_call_id=step.call_id)
        )

    reply = client.complete(
        messages,
        tool_specs(
            include_memory=context.memory,
            include_reference=context.reference_tools,
            include_live=context.live_tools,
        ),
    )
    if reply.tool_calls:
        call = reply.tool_calls[0]
        return {"kind": "tool", "name": call.name, "arguments": call.arguments, "call_id": call.id}
    return {"kind": "final"}
