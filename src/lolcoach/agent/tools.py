"""The deterministic tool surface the agent orchestrates.

Every tool wraps existing engine/knowledge logic; none of them is the LLM. In
particular ``suggest_pick`` is the **only** source of pick recommendations — the
agent may call it, but it may not invent a pick. ``set_board`` is the fuzzy-input
adapter: it resolves human-typed champion names (typos, aliases, shorthand) to
canonical slugs via :class:`~lolcoach.knowledge.resolve.NameResolver`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from lolcoach.constants import LANES
from lolcoach.engine.draft_state import DraftState
from lolcoach.engine.explain import recommendation_to_dict, trace_to_dict
from lolcoach.engine.scoring import evaluate_draft
from lolcoach.knowledge.ontology import Ontology, Relation
from lolcoach.knowledge.resolve import NameResolver
from lolcoach.values import as_str, opt_list

logger = logging.getLogger(__name__)

Board = dict[str, list[str]]

_BOARD_KEYS = ("allies", "enemies", "ally_bans", "enemy_bans", "open_roles")


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool
    payload: dict[str, object]


def empty_board() -> Board:
    return {key: [] for key in _BOARD_KEYS}


def normalize_board(value: object) -> Board:
    """Coerce an arbitrary board payload into the canonical shape (slugs)."""
    raw = value if isinstance(value, dict) else {}
    board = empty_board()
    for key in _BOARD_KEYS:
        board[key] = [as_str(item) for item in opt_list(raw.get(key)) if as_str(item)]
    return board


def _draft_state(board: Board) -> DraftState:
    open_roles = tuple(board.get("open_roles") or ())
    return DraftState(
        allies=tuple(board.get("allies", ())),
        enemies=tuple(board.get("enemies", ())),
        ally_bans=tuple(board.get("ally_bans", ())),
        enemy_bans=tuple(board.get("enemy_bans", ())),
        open_roles=open_roles or LANES,
    )


def _edge_to_dict(relation: Relation) -> dict[str, object]:
    return {
        "type": relation.type,
        "source": relation.source,
        "target": relation.target,
        "position": relation.position,
        "weight": relation.weight,
        "win_rate": relation.win_rate,
        "games": relation.games,
        "reason": relation.reason,
    }


class ToolBox:
    """Dispatches tool calls against a loaded ontology and the current board."""

    def __init__(self, ontology: Ontology) -> None:
        self._ontology = ontology
        self._resolver = NameResolver(ontology)

    def call(self, name: str, args: dict[str, object], board: Board) -> ToolResult:
        if name == "set_board":
            return self.set_board(args)
        if name == "suggest_pick":
            return self.suggest_pick(board)
        if name == "explain_matchup":
            return self.explain_matchup(args)
        if name == "get_champion":
            return self.get_champion(args)
        return ToolResult(name, False, {"error": f"unknown tool {name!r}"})

    def set_board(self, args: Mapping[str, object]) -> ToolResult:
        unresolved: list[str] = []

        def resolve_list(key: str) -> list[str]:
            slugs: list[str] = []
            for item in opt_list(args.get(key)):
                text = as_str(item)
                slug = self._resolver.resolve(text) if text else None
                if slug is None:
                    if text:
                        unresolved.append(text)
                else:
                    slugs.append(slug)
            return slugs

        open_roles = [
            role
            for role in (as_str(item) for item in opt_list(args.get("open_roles")))
            if role in LANES
        ]
        board: Board = {
            "allies": resolve_list("allies"),
            "enemies": resolve_list("enemies"),
            "ally_bans": resolve_list("ally_bans"),
            "enemy_bans": resolve_list("enemy_bans"),
            "open_roles": open_roles,
        }
        return ToolResult("set_board", True, {"board": board, "unresolved": unresolved})

    def suggest_pick(self, board: Board) -> ToolResult:
        state = _draft_state(board)
        recommendation = evaluate_draft(self._ontology, state)
        payload: dict[str, object] = recommendation_to_dict(recommendation)
        payload["traces"] = [trace_to_dict(line) for line in recommendation.traces()]
        payload["problems"] = state.problems()
        payload["board"] = board
        return ToolResult("suggest_pick", True, payload)

    def explain_matchup(self, args: dict[str, object]) -> ToolResult:
        first = self._resolve_arg(args, "champion_a", "a")
        second = self._resolve_arg(args, "champion_b", "b")
        if first is None or second is None:
            missing = [
                as_str(args.get("champion_a")) or as_str(args.get("a")),
                as_str(args.get("champion_b")) or as_str(args.get("b")),
            ]
            return ToolResult(
                "explain_matchup",
                False,
                {"error": "could not resolve both champions", "unresolved": missing},
            )
        role = as_str(args.get("role")) or None
        pair = {first, second}
        edges = [
            _edge_to_dict(relation)
            for relation in self._ontology.relations
            if {relation.source, relation.target} == pair
            and (role is None or relation.position == role)
        ]
        return ToolResult(
            "explain_matchup", True, {"champion_a": first, "champion_b": second, "edges": edges}
        )

    def get_champion(self, args: dict[str, object]) -> ToolResult:
        query = as_str(args.get("champion")) or as_str(args.get("name"))
        slug = self._resolver.resolve(query) if query else None
        if slug is None:
            return ToolResult("get_champion", False, {"error": "unknown champion", "query": query})
        champion = self._ontology.champion(slug)
        if champion is None:  # pragma: no cover - resolver only returns known slugs
            return ToolResult("get_champion", False, {"error": "unknown champion", "query": query})
        return ToolResult(
            "get_champion",
            True,
            {
                "slug": slug,
                "name": champion.name,
                "positions": [
                    {
                        "position": position.position,
                        "win_rate": position.win_rate,
                        "pick_rate": position.pick_rate,
                        "ban_rate": position.ban_rate,
                        "tier": position.tier,
                    }
                    for position in champion.positions
                ],
            },
        )

    def _resolve_arg(self, args: dict[str, object], *keys: str) -> str | None:
        for key in keys:
            query = as_str(args.get(key))
            if query:
                slug = self._resolver.resolve(query)
                if slug is not None:
                    return slug
        return None


def champions_in_payloads(ontology: Ontology, payloads: list[object]) -> set[str]:
    """Every known champion slug that appears anywhere in a set of tool payloads."""
    slugs = set(ontology.champions)
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            if value in slugs:
                found.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for payload in payloads:
        walk(payload)
    return found


def tool_specs() -> list[dict[str, object]]:
    """Tool schemas in OpenAI function format (LangChain normalizes per provider)."""

    def fn(name: str, description: str, parameters: dict[str, object]) -> dict[str, object]:
        return {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": parameters},
        }

    names = {"type": "array", "items": {"type": "string"}}
    return [
        fn(
            "set_board",
            "Set the current draft board. Names may be loose (aliases, typos, casing); "
            "they are resolved to canonical champions. Use for both teams' picks and bans.",
            {
                "type": "object",
                "properties": {
                    "allies": {**names, "description": "our team's picks"},
                    "enemies": {**names, "description": "the enemy team's picks"},
                    "ally_bans": names,
                    "enemy_bans": names,
                    "open_roles": {
                        **names,
                        "description": f"roles still open for us, subset of {list(LANES)}",
                    },
                },
            },
        ),
        fn(
            "suggest_pick",
            "Run the deterministic engine on the current board and return the single best "
            "counter-pick and synergy-pick with their evidence traces. This is the ONLY "
            "source of pick recommendations; never state a pick that did not come from here.",
            {"type": "object", "properties": {}},
        ),
        fn(
            "explain_matchup",
            "Return the measured counter and synergy edges between two champions "
            "(optionally in a specific role). Evidence only — not a recommendation.",
            {
                "type": "object",
                "properties": {
                    "champion_a": {"type": "string"},
                    "champion_b": {"type": "string"},
                    "role": {"type": "string", "description": f"one of {list(LANES)}"},
                },
                "required": ["champion_a", "champion_b"],
            },
        ),
        fn(
            "get_champion",
            "Return a champion's roles and win/pick/ban rates from the snapshot.",
            {
                "type": "object",
                "properties": {"champion": {"type": "string"}},
                "required": ["champion"],
            },
        ),
    ]
