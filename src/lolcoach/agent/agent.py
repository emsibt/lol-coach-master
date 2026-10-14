"""The agent's decision step: which tool to call next, or stop.

This is the *only* place the LLM is consulted, and it is consulted about
orchestration — not about League. With a client it runs a tool-loop over the
deterministic tools; without one it falls back to a small deterministic planner
so the whole feature works key-less. Both paths emit the same JSON-safe action
dict, which the agent workflow persists and the Trace Inspector renders.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from lolcoach.agent.llm import ImagePart, LlmClient, LlmMessage, LlmToolCall
from lolcoach.agent.tools import Board, tool_specs
from lolcoach.config import Settings
from lolcoach.knowledge.ontology import Ontology
from lolcoach.knowledge.resolve import NameResolver
from lolcoach.values import as_str, opt_dict

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
- Every champion, number, and matchup in your answer must come from a tool
  result. If the tools are silent on something, say so.
- Never mention the engine, the knowledge graph, or "the model".
"""

_IMAGE_OFFLINE = (
    "I can't read a draft image without a model key configured. Type the picks "
    "(for example: `jinx vs their zeri`) and I'll work from there."
)

_NO_CHAMPIONS = (
    "I couldn't find any champion names in that. Tell me who is picked - for "
    "example `jinx vs their zeri` - or describe our side and theirs."
)

_WORD_RE = re.compile(r"[a-z0-9']+")

_VS_TOKENS = {"vs", "v", "versus", "against"}
_ENEMY_TOKENS = {"their", "they", "enemy", "enemies", "them"}
_ALLY_TOKENS = {"our", "ours", "we", "us", "my", "ally", "allies"}
_BAN_TOKENS = {"ban", "bans", "banning", "banned"}


@dataclass(frozen=True, slots=True)
class ToolStep:
    """One completed tool call: the request, and its result."""

    call_id: str
    name: str
    arguments: dict[str, object]
    ok: bool
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class AgentContext:
    question: str
    board: Board
    image: ImagePart | None = None
    history: tuple[ToolStep, ...] = ()
    turns: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class ParsedInput:
    """A loose user message reduced to champion slugs per side."""

    allies: list[str] = field(default_factory=list)
    enemies: list[str] = field(default_factory=list)
    ally_bans: list[str] = field(default_factory=list)
    enemy_bans: list[str] = field(default_factory=list)

    def any(self) -> bool:
        return bool(self.allies or self.enemies or self.ally_bans or self.enemy_bans)

    def as_args(self) -> dict[str, object]:
        return {
            "allies": list(self.allies),
            "enemies": list(self.enemies),
            "ally_bans": list(self.ally_bans),
            "enemy_bans": list(self.enemy_bans),
        }


def step_to_dict(step: ToolStep) -> dict[str, object]:
    return {
        "call_id": step.call_id,
        "name": step.name,
        "arguments": step.arguments,
        "ok": step.ok,
        "payload": step.payload,
    }


def step_from_dict(value: object) -> ToolStep:
    raw = opt_dict(value)
    return ToolStep(
        call_id=as_str(raw.get("call_id")),
        name=as_str(raw.get("name")),
        arguments=opt_dict(raw.get("arguments")),
        ok=bool(raw.get("ok")),
        payload=opt_dict(raw.get("payload")),
    )


def parse_mentions(text: str, ontology: Ontology) -> ParsedInput:
    """Greedily resolve champion names in free text and assign them to a side.

    Sides come from the nearest preceding marker: ``vs``/``their``/``enemy`` put
    us on the enemy side, ``our``/``we`` put us back on ours, and ``ban`` flags
    the mention as a ban. Champion names are matched longest-first so
    "twisted fate" beats "fate".
    """
    resolver = NameResolver(ontology)
    words = _WORD_RE.findall(text.lower())
    consumed: set[int] = set()
    mentions: list[tuple[int, str]] = []
    for size in (3, 2, 1):
        for start in range(len(words) - size + 1):
            span = range(start, start + size)
            if any(index in consumed for index in span):
                continue
            slug = resolver.resolve(" ".join(words[start : start + size]))
            if slug is None:
                continue
            consumed.update(span)
            mentions.append((start, slug))

    parsed = ParsedInput()
    seen: set[str] = set()
    for index, slug in mentions:
        if slug in seen:
            continue
        seen.add(slug)
        side, banned = _side_before(words, index)
        if banned:
            target = parsed.enemy_bans if side == "enemy" else parsed.ally_bans
        else:
            target = parsed.enemies if side == "enemy" else parsed.allies
        target.append(slug)
    return parsed


def _side_before(words: list[str], index: int) -> tuple[str, bool]:
    side = "ally"
    banned = False
    for token in words[:index]:
        if token in _VS_TOKENS or token in _ENEMY_TOKENS:
            side = "enemy"
        elif token in _ALLY_TOKENS:
            side = "ally"
        if token in _BAN_TOKENS:
            banned = True
    return side, banned


def choose_action(
    context: AgentContext,
    *,
    client: LlmClient | None,
    settings: Settings,
    ontology: Ontology,
) -> dict[str, object]:
    """Return the next action as a JSON-safe dict: a tool call or a final answer."""
    if client is not None:
        try:
            return _llm_action(context, client, settings)
        except Exception:  # noqa: BLE001 - any provider failure degrades to offline
            logger.warning("LLM action failed; falling back to offline planner", exc_info=True)
    return _offline_action(context, ontology)


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

    reply = client.complete(messages, tool_specs())
    if reply.tool_calls:
        call = reply.tool_calls[0]
        return {"kind": "tool", "name": call.name, "arguments": call.arguments, "call_id": call.id}
    return {"kind": "final"}


def _offline_action(context: AgentContext, ontology: Ontology) -> dict[str, object]:
    if context.image is not None and not context.question.strip():
        return {"kind": "final"}

    if not context.history:
        parsed = parse_mentions(context.question, ontology)
        if parsed.any():
            return {"kind": "tool", "name": "set_board", "arguments": parsed.as_args()}
        if _board_has_picks(context.board):
            return {"kind": "tool", "name": "suggest_pick", "arguments": {}}
        return {"kind": "final"}

    last = context.history[-1]
    if last.name == "set_board":
        if not _board_has_picks(context.board):
            return {"kind": "final"}
        return {"kind": "tool", "name": "suggest_pick", "arguments": {}}
    return {"kind": "final"}


def _board_has_picks(board: Board) -> bool:
    return bool(board.get("allies") or board.get("enemies"))


def offline_hint(context: AgentContext) -> str:
    """A deterministic, user-facing fallback when no tool produced evidence.

    Used by the offline path and by narration when a run stops without a usable
    recommendation (for example an image with no readable text).
    """
    if context.image is not None and not context.history:
        return _IMAGE_OFFLINE
    if not context.history:
        return _NO_CHAMPIONS
    return ""
