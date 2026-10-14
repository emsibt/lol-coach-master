"""Deterministic explanations of an engine recommendation.

These are pure formatting helpers over the engine's own output — the offline
fallback text and the payload the API/narrator hand downstream. They assert
nothing that is not already in the traces.
"""

from __future__ import annotations

from lolcoach.engine.scoring import Pick, Recommendation
from lolcoach.engine.trace import TraceLine

_NONE = "  none available"


def trace_to_dict(line: TraceLine) -> dict[str, object]:
    return {"kind": line.kind, "source": line.source, "text": line.text, "delta": line.delta}


def pick_to_dict(pick: Pick) -> dict[str, object]:
    return {
        "slug": pick.slug,
        "name": pick.name,
        "role": pick.role,
        "score": pick.score,
        "traces": [trace_to_dict(line) for line in pick.traces],
    }


def recommendation_to_dict(recommendation: Recommendation) -> dict[str, object]:
    return {
        "counter": pick_to_dict(recommendation.counter) if recommendation.counter else None,
        "synergy": pick_to_dict(recommendation.synergy) if recommendation.synergy else None,
    }


def describe_pick(pick: Pick) -> str:
    lines = [f"{pick.name} ({pick.role}) - score {pick.score:+.3f}"]
    lines.extend(f"  - [{line.kind}] {line.text} ({line.delta:+.3f})" for line in pick.traces)
    return "\n".join(lines)


def describe(recommendation: Recommendation) -> str:
    lines = ["Counter-pick:"]
    lines.append(describe_pick(recommendation.counter) if recommendation.counter else _NONE)
    lines.append("Synergy-pick:")
    lines.append(describe_pick(recommendation.synergy) if recommendation.synergy else _NONE)
    return "\n".join(lines)
