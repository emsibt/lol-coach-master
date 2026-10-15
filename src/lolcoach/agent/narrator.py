"""The grounded narrator: tool results in, prose out.

The narrator exists to *rephrase*, never to decide. Two entry points:

* :func:`narrate` — explain one engine recommendation record (the draft-eval path).
* :func:`narrate_final` — turn a whole agent tool-loop into a coach-voice answer.

Both may not assert any champion, number, or matchup that is not present in their
input. For :func:`narrate_final` that is enforced structurally by
:func:`unsourced_champions`: if the model names a champion that appears in no tool
result, the answer is discarded and the deterministic offline summary is returned
instead. :func:`narrate_final` requires a model client and raises
:class:`LlmUnavailableError` without one, so the agent never fabricates an answer;
:func:`narrate` (the draft-eval path) still falls back to the offline summary.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from lolcoach.agent.agent import ToolStep
from lolcoach.agent.llm import LlmClient, LlmMessage, LlmUnavailableError
from lolcoach.agent.tools import champions_in_payloads
from lolcoach.config import Settings, get_settings
from lolcoach.knowledge.ontology import Ontology
from lolcoach.values import as_str, opt_dict, opt_list

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a League of Legends draft coach. You are given a JSON record of a "
    "recommendation produced by a deterministic engine: a counter-pick and a "
    "synergy-pick, each with source-tagged traces. Explain the recommendation in "
    "clear, confident prose. You must not invent or infer any champion, statistic, "
    "number, or reason that is not explicitly present in the JSON. Do not suggest "
    "picks other than the ones given. If the record is empty, say no recommendation "
    "is available."
)

_FINAL_SYSTEM_PROMPT = (
    "You are a League of Legends draft coach talking to a player. You are given "
    "the player's question, the current draft board, and the JSON results of the "
    "tools that were run for them. Answer in a short, direct coach voice. Every "
    "champion, number, and matchup you mention must appear in the tool results - "
    "never add one from your own knowledge, and never recommend a pick that the "
    "suggest_pick result did not name. Do not quote a raw score on its own; give "
    "the evidence in words. Never mention engines, ontologies, models, or tools. "
    "When you cite tips or a build, do not name another champion: the reference "
    "text mentions other champions, but they are not part of this draft. "
    "If the tool results are silent, say plainly that there is nothing measured."
)

_NOTHING = "There's nothing measured for that yet - tell me the picks and I'll run it."

# Tools whose payloads are *prose about* a champion rather than structured facts
# about the draft. A tip can mention half the roster ("great against Yasuo"), so
# walking these payloads would whitelist champions that have nothing to do with
# this draft. They source exactly what they declare in ``ToolResult.sources``.
_EXPLICIT_SOURCES = frozenset(
    {
        "get_summoner_profile",
        "get_champion_tips",
        "get_champion_build",
        "get_lane_matchup_guide",
    }
)


def allowed_champions(
    ontology: Ontology, tool_results: Sequence[ToolStep], board: dict[str, list[str]]
) -> set[str]:
    """The champions a narration of these tool results is allowed to name.

    Every other tool keeps the original behavior — its whole payload is walked
    for known slugs, plus the board. The prose tools contribute ``step.sources``
    only, so reference text can never widen the whitelist.
    """
    allowed: set[str] = set()
    walked: list[object] = [board]
    for step in tool_results:
        if step.name in _EXPLICIT_SOURCES:
            allowed.update(step.sources)
        else:
            walked.append(step.payload)
    return allowed | champions_in_payloads(ontology, walked)


@dataclass(frozen=True, slots=True)
class Narration:
    text: str
    mode: str  # "llm" or "offline"


def narrate(
    recommendation: dict[str, object],
    *,
    question: str = "",
    client: LlmClient | None = None,
    settings: Settings | None = None,
) -> Narration:
    """Explain ``recommendation`` in prose, grounded strictly in its traces."""
    active = settings or get_settings()
    model_client = client if client is not None else _default_client(active)
    if model_client is not None:
        messages = [
            LlmMessage("system", _SYSTEM_PROMPT),
            LlmMessage("user", _recommendation_prompt(recommendation, question)),
        ]
        try:
            reply = model_client.complete(messages, [])
            if reply.text.strip():
                return Narration(text=reply.text, mode="llm")
        except Exception:
            logger.exception("Narrator model call failed; using offline summary")
    return Narration(text=offline_text(recommendation), mode="offline")


def narrate_final(
    question: str,
    tool_results: Sequence[ToolStep],
    board: dict[str, list[str]],
    *,
    client: LlmClient | None = None,
    settings: Settings | None = None,
    ontology: Ontology | None = None,
    fallback: str = "",
) -> Narration:
    """Turn a whole agent tool-loop into a grounded coach answer.

    A model client is required: without one, or if the call fails, this raises
    :class:`LlmUnavailableError` so the execution fails rather than returning a
    fabricated summary. The model may phrase the answer; it may not source any
    champion outside the tool results. A reply that breaks that rule is dropped
    for the grounded offline summary.
    """
    active = settings or get_settings()
    model_client = client if client is not None else _default_client(active)
    if model_client is None:
        raise LlmUnavailableError("agent chat requires an LLM, but none is configured")
    text = _final_llm_text(model_client, question, tool_results, board)
    if ontology is not None:
        allowed = allowed_champions(ontology, tool_results, board)
        invented = unsourced_champions(text, allowed, ontology)
        if invented:
            logger.warning(
                "Narration named unsourced champions; using offline summary",
                extra={"data": {"unsourced": sorted(invented)}},
            )
            return Narration(
                text=offline_final_text(tool_results, ontology, fallback=fallback),
                mode="offline",
            )
    if not text.strip():
        raise LlmUnavailableError("the model returned an empty answer")
    return Narration(text=text, mode="llm")


def unsourced_champions(text: str, allowed_slugs: set[str], ontology: Ontology) -> set[str]:
    """Known champions named in ``text`` that appear in no allowed source.

    Matches canonical names and slugs (case-insensitive, word-bounded). Aliases
    are not scanned: they are the resolver's job, not the guard's, and a stray
    alias in prose is far more likely to be an ordinary word.
    """
    haystack = text.lower()
    found: set[str] = set()
    for slug, champion in ontology.champions.items():
        if slug in allowed_slugs:
            continue
        for needle in {champion.name.lower(), slug}:
            if not needle:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack):
                found.add(slug)
                break
    return found


def offline_text(recommendation: dict[str, object]) -> str:
    """A deterministic summary built only from the record's own trace text."""
    blocks = [
        _pick_block("Counter-pick", opt_dict(recommendation.get("counter"))),
        _pick_block("Synergy-pick", opt_dict(recommendation.get("synergy"))),
    ]
    return "\n".join(blocks)


def offline_final_text(
    tool_results: Sequence[ToolStep], ontology: Ontology, *, fallback: str = ""
) -> str:
    """A deterministic, grounded answer assembled from tool payloads."""
    suggest = _last_payload(tool_results, "suggest_pick")
    if suggest is not None:
        return offline_text(suggest)
    matchup = _last_payload(tool_results, "explain_matchup")
    if matchup is not None:
        return _matchup_block(matchup, ontology)
    champion = _last_payload(tool_results, "get_champion")
    if champion is not None:
        return _champion_block(champion, ontology)
    tips = _last_payload(tool_results, "get_champion_tips")
    if tips is not None:
        return _tips_block(tips)
    build = _last_payload(tool_results, "get_champion_build")
    if build is not None:
        return _build_block(build)
    matchup_guide = _last_payload(tool_results, "get_lane_matchup_guide")
    if matchup_guide is not None:
        return _guide_block(matchup_guide)
    profile = _last_payload(tool_results, "get_summoner_profile")
    if profile is not None:
        return _profile_block(profile)
    return fallback or _NOTHING


def _default_client(settings: Settings) -> LlmClient | None:
    from lolcoach.agent.llm import build_client

    return build_client(settings)


def _last_payload(tool_results: Sequence[ToolStep], name: str) -> dict[str, object] | None:
    for step in reversed(tool_results):
        if step.name == name and step.ok:
            return step.payload
    return None


def _recommendation_prompt(recommendation: dict[str, object], question: str) -> str:
    payload = json.dumps(recommendation, ensure_ascii=False, sort_keys=True)
    prompt = f"Recommendation (JSON):\n{payload}"
    if question:
        prompt += f"\n\nQuestion: {question}"
    return prompt


def _final_llm_text(
    client: LlmClient,
    question: str,
    tool_results: Sequence[ToolStep],
    board: dict[str, list[str]],
) -> str:
    record = {
        "question": question,
        "board": board,
        "tool_results": [
            {"tool": step.name, "ok": step.ok, "result": step.payload} for step in tool_results
        ],
    }
    messages = [
        LlmMessage("system", _FINAL_SYSTEM_PROMPT),
        LlmMessage("user", json.dumps(record, ensure_ascii=False, sort_keys=True)),
    ]
    return client.complete(messages, []).text


def _pick_block(label: str, pick: dict[str, object]) -> str:
    if not pick:
        return f"{label}: none available."
    name = as_str(pick.get("name"), "unknown")
    role = as_str(pick.get("role"), "unknown")
    lines = [f"{label}: {name} ({role})."]
    for trace in opt_list(pick.get("traces")):
        text = as_str(opt_dict(trace).get("text"))
        if text:
            lines.append(f"  - {text}")
    return "\n".join(lines)


def _matchup_block(payload: dict[str, object], ontology: Ontology) -> str:
    first = _display(payload.get("champion_a"), ontology)
    second = _display(payload.get("champion_b"), ontology)
    lines = [f"{first} vs {second}:"]
    edges = opt_list(payload.get("edges"))
    if not edges:
        lines.append("  no measured history in this role.")
        return "\n".join(lines)
    for edge in edges:
        reason = as_str(opt_dict(edge).get("reason"))
        if reason:
            lines.append(f"  - {reason}")
    return "\n".join(lines)


def _champion_block(payload: dict[str, object], ontology: Ontology) -> str:
    name = as_str(payload.get("name")) or _display(payload.get("slug"), ontology)
    lines = [f"{name}:"]
    for position in opt_list(payload.get("positions")):
        info = opt_dict(position)
        role = as_str(info.get("position"), "?")
        win_rate = info.get("win_rate")
        share = f"{win_rate:.1%}" if isinstance(win_rate, (int, float)) else "?"
        lines.append(f"  - {role}: {share} win rate")
    return "\n".join(lines)


def _tips_block(payload: dict[str, object]) -> str:
    name = as_str(payload.get("name")) or as_str(payload.get("slug"), "That champion")
    if not payload.get("available", True):
        return f"{name}: no tips are recorded for them yet."
    lines = [f"{name}:"]
    abilities = opt_list(payload.get("abilities"))
    if abilities:
        lines.append("  Abilities:")
        for ability in abilities:
            info = opt_dict(ability)
            slot = as_str(info.get("slot"), "?")
            lines.append(f"    - {slot} {as_str(info.get('name'))}")
    for label, key in (("Playing as", "ally_tips"), ("Playing against", "enemy_tips")):
        tips = [text for text in (as_str(item) for item in opt_list(payload.get(key))) if text]
        if tips:
            lines.append(f"  {label}:")
            lines.extend(f"    - {tip}" for tip in tips)
    return "\n".join(lines)


def _build_block(payload: dict[str, object]) -> str:
    name = as_str(payload.get("name")) or as_str(payload.get("slug"), "That champion")
    if not payload.get("available", True):
        return f"{name}: no build is recorded for them yet."
    role = as_str(payload.get("position"))
    lines = [f"{name} ({role}):" if role else f"{name}:"]
    labels = {"starter": "Start", "boots": "Boots", "core": "Core"}
    for item_set in opt_list(payload.get("items")):
        info = opt_dict(item_set)
        names = [text for text in (as_str(x) for x in opt_list(info.get("names"))) if text]
        if names:
            item_type = as_str(info.get("item_type"))
            lines.append(f"  {labels.get(item_type, item_type)}: {', '.join(names)}")
    for page in opt_list(payload.get("runes")):
        info = opt_dict(page)
        picks = [text for text in (as_str(x) for x in opt_list(info.get("picks"))) if text]
        if picks:
            lines.append(f"  Runes - {as_str(info.get('page'))}: {', '.join(picks)}")
    order = [text for text in (as_str(x) for x in opt_list(payload.get("skill_order"))) if text]
    if order:
        lines.append(f"  Skill order: {' > '.join(order)}")
    return "\n".join(lines)


def _guide_block(payload: dict[str, object]) -> str:
    champion = as_str(payload.get("champion"), "?")
    opponent = as_str(payload.get("opponent"), "?")
    role = as_str(payload.get("position"), "?")
    lines = [f"{champion} vs {opponent} ({role}):"]
    win_rate = payload.get("champion_win_rate")
    games = payload.get("games")
    if isinstance(win_rate, (int, float)) and win_rate > 0:
        share = f"{win_rate:.1%}"
        lines.append(f"  - {champion} wins {share} of this lane" + _over(games))
    for label, key in (
        ("Lane advantage", "lane_advantage"),
        ("Solo-kill advantage", "solo_kill_advantage"),
        ("Play style", "play_style"),
    ):
        value = as_str(payload.get(key))
        if value:
            lines.append(f"  - {label}: {value}")
    tip = as_str(payload.get("tip"))
    if tip:
        lines.append(f"  - {tip}")
    return "\n".join(lines)


def _profile_block(payload: dict[str, object]) -> str:
    riot_id = as_str(payload.get("riot_id"), "that player")
    region = as_str(payload.get("region"))
    queues = {"SOLORANKED": "Solo/Duo", "FLEXRANKED": "Flex"}
    lines = [f"{riot_id}" + (f" ({region})" if region else "") + ":"]
    entries = opt_list(payload.get("queues"))
    if not entries:
        lines.append("  no ranked record available.")
    for entry in entries:
        info = opt_dict(entry)
        tier = as_str(info.get("tier"), "unranked")
        division = info.get("division")
        lp = info.get("lp")
        placement = tier.title()
        if isinstance(division, int) and division:
            placement += f" {division}"
        if isinstance(lp, int):
            placement += f", {lp} LP"
        wins, losses = info.get("wins"), info.get("losses")
        record = (
            f" ({wins}-{losses})"
            if isinstance(wins, int) and isinstance(losses, int)
            else ""
        )
        queue = as_str(info.get("queue"), "?")
        lines.append(f"  - {queues.get(queue, queue)}: {placement}{record}")
    return "\n".join(lines)


def _over(games: object) -> str:
    return f" over {games:,} games" if isinstance(games, int) and games else ""


def _display(slug_value: object, ontology: Ontology) -> str:
    slug = as_str(slug_value, "?")
    champion = ontology.champion(slug)
    return champion.name if champion is not None else slug
