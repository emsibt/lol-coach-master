"""The grounded narrator: tool results in, prose out.

The narrator exists to *rephrase*, never to decide. Two entry points:

* :func:`narrate` — explain one engine recommendation record (the draft-eval path).
* :func:`narrate_final` — turn a whole agent tool-loop into a coach-voice answer.

Both may not assert any champion, number, or matchup that is not present in their
input. For :func:`narrate_final` that is enforced structurally by
:func:`unsourced_champions`: if the model names a champion that appears in no tool
result, the answer is discarded and the deterministic offline summary is returned
instead. Without a client both paths fall back to that offline summary, so the
agent stays grounded and functional key-less.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from lolcoach.agent.agent import ToolStep
from lolcoach.agent.llm import LlmClient, LlmMessage
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
    "If the tool results are silent, say plainly that there is nothing measured."
)

_NOTHING = "There's nothing measured for that yet - tell me the picks and I'll run it."


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

    The model may phrase the answer; it may not source any champion outside the
    tool results. A reply that breaks that rule is dropped for the offline text.
    """
    active = settings or get_settings()
    model_client = client if client is not None else _default_client(active)
    if model_client is not None:
        try:
            text = _final_llm_text(model_client, question, tool_results, board)
        except Exception:
            logger.exception("Final narration failed; using offline summary")
        else:
            allowed: set[str] = set()
            if ontology is not None:
                allowed = champions_in_payloads(
                    ontology, [step.payload for step in tool_results] + [board]
                )
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
            if text.strip():
                return Narration(text=text, mode="llm")
    if ontology is None:
        return Narration(text=fallback or _NOTHING, mode="offline")
    return Narration(
        text=offline_final_text(tool_results, ontology, fallback=fallback), mode="offline"
    )


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


def _display(slug_value: object, ontology: Ontology) -> str:
    slug = as_str(slug_value, "?")
    champion = ontology.champion(slug)
    return champion.name if champion is not None else slug
