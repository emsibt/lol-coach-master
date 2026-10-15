"""The deterministic tool surface the agent orchestrates.

Every tool wraps existing engine/knowledge logic; none of them is the LLM. In
particular ``suggest_pick`` is the **only** source of pick recommendations — the
agent may call it, but it may not invent a pick. ``set_board`` is the fuzzy-input
adapter: it resolves human-typed champion names (typos, aliases, shorthand) to
canonical slugs via :class:`~lolcoach.knowledge.resolve.NameResolver`.

Two kinds of tool return *evidence* rather than a decision:
``get_champion_tips`` / ``get_champion_build`` read the materialized reference
data, and the live tools (``get_summoner_profile`` / ``get_lane_matchup_guide``)
read a :class:`LiveDataProvider`. Because their payloads are prose that can name
other champions, they declare ``ToolResult.sources`` — the champions they are
entitled to source — and the grounding guard whitelists only those, never the
champions their prose happens to mention.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from lolcoach.chat.memory import memory_tool_specs
from lolcoach.constants import LANES
from lolcoach.engine.draft_state import DraftState
from lolcoach.engine.explain import recommendation_to_dict, trace_to_dict
from lolcoach.engine.scoring import evaluate_draft
from lolcoach.knowledge.ontology import Ontology, Relation
from lolcoach.knowledge.reference import ReferenceData
from lolcoach.knowledge.resolve import NameResolver
from lolcoach.text import upper_snake
from lolcoach.values import as_str, opt_list

logger = logging.getLogger(__name__)

Board = dict[str, list[str]]

_BOARD_KEYS = ("allies", "enemies", "ally_bans", "enemy_bans", "open_roles")

# A tool's name/description/JSON-schema -> an OpenAI function spec.
_SpecFn = Callable[[str, str, dict[str, object]], dict[str, object]]


class LiveDataProvider(Protocol):
    """Per-user and per-matchup data that is fetched on demand, not materialized.

    Implemented outside this layer (``dataprovider/live.py``) and injected, so
    ``agent/**`` never imports the network layer. An implementation raises
    :class:`LiveDataError` on failure; the loop turns that into a failed step.
    """

    def summoner_profile(self, riot_id: str, region: str = "") -> dict[str, object]: ...

    def lane_matchup(
        self, champion: str, opponent: str, role: str
    ) -> dict[str, object]: ...


class LiveDataError(RuntimeError):
    """Raised by a :class:`LiveDataProvider` when a live lookup cannot be served."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool
    payload: dict[str, object]
    # Champion slugs this result is entitled to source. Empty means "none of its
    # own" — the narrator then falls back to walking the payload (see narrator.py).
    sources: tuple[str, ...] = ()


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


def _opgg_name(ontology: Ontology, slug: str) -> str:
    """The UPPER_SNAKE identifier OP.GG accepts for ``slug``.

    Built from the display name, not the Riot key: OP.GG squashes punctuation, so
    ``Cho'Gath`` is ``CHOGATH`` while Riot's key ``Chogath``-style form differs for
    names like ``Dr. Mundo`` (``DR_MUNDO``, not ``DRMUNDO``).
    """
    champion = ontology.champion(slug)
    return upper_snake(champion.name if champion is not None else slug)


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

    def __init__(
        self,
        ontology: Ontology,
        *,
        reference: ReferenceData | None = None,
        live: LiveDataProvider | None = None,
    ) -> None:
        self._ontology = ontology
        self._resolver = NameResolver(ontology)
        self._reference = reference
        self._live = live

    @property
    def reference_tools(self) -> bool:
        """Whether the materialized reference tools have anything to serve."""
        return self._reference is not None and not self._reference.is_empty

    @property
    def live_tools(self) -> bool:
        return self._live is not None

    def call(self, name: str, args: dict[str, object], board: Board) -> ToolResult:
        if name == "set_board":
            return self.set_board(args)
        if name == "suggest_pick":
            return self.suggest_pick(board)
        if name == "explain_matchup":
            return self.explain_matchup(args)
        if name == "get_champion":
            return self.get_champion(args)
        if name == "get_champion_tips":
            return self.get_champion_tips(args)
        if name == "get_champion_build":
            return self.get_champion_build(args)
        if name == "get_summoner_profile":
            return self.get_summoner_profile(args)
        if name == "get_lane_matchup_guide":
            return self.get_lane_matchup_guide(args)
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

    def get_champion_tips(self, args: dict[str, object]) -> ToolResult:
        slug = self._resolve_arg(args, "champion", "name")
        if slug is None:
            query = self._query(args, "champion", "name")
            payload: dict[str, object] = {"error": "unknown champion", "query": query}
            return ToolResult("get_champion_tips", False, payload)
        champion = self._ontology.champion(slug)
        name = champion.name if champion is not None else slug
        reference = self._reference.champion(slug) if self._reference is not None else None
        if reference is None:
            return ToolResult(
                "get_champion_tips",
                True,
                {"slug": slug, "name": name, "available": False},
                sources=(slug,),
            )
        return ToolResult(
            "get_champion_tips",
            True,
            {
                "slug": slug,
                "name": name,
                "lore": reference.lore,
                "blurb": reference.blurb,
                "tags": list(reference.tags),
                "ally_tips": list(reference.ally_tips),
                "enemy_tips": list(reference.enemy_tips),
                "abilities": [
                    {"slot": ability.slot, "name": ability.name,
                     "description": ability.description}
                    for ability in reference.abilities
                ],
            },
            sources=(slug,),
        )

    def get_champion_build(self, args: dict[str, object]) -> ToolResult:
        slug = self._resolve_arg(args, "champion", "name")
        if slug is None:
            query = self._query(args, "champion", "name")
            payload: dict[str, object] = {"error": "unknown champion", "query": query}
            return ToolResult("get_champion_build", False, payload)
        champion = self._ontology.champion(slug)
        name = champion.name if champion is not None else slug
        requested = as_str(args.get("role")) or as_str(args.get("position")) or None
        role = requested if requested in LANES else None
        build = self._reference.build(slug, role) if self._reference is not None else None
        if build is None:
            return ToolResult(
                "get_champion_build",
                True,
                {"slug": slug, "name": name, "available": False, "role": requested},
                sources=(slug,),
            )
        return ToolResult(
            "get_champion_build",
            True,
            {
                "slug": slug,
                "name": name,
                "position": build.position,
                "skill_order": list(build.skill_order),
                "items": [
                    {
                        "item_type": item_set.item_type,
                        "names": list(item_set.names),
                        "win_rate": item_set.win_rate,
                        "pick_rate": item_set.pick_rate,
                    }
                    for item_set in build.item_sets
                ],
                "runes": [
                    {"page": page.page, "picks": list(page.picks)} for page in build.runes
                ],
            },
            sources=(slug,),
        )

    def get_summoner_profile(self, args: dict[str, object]) -> ToolResult:
        """Rank and record for a public Riot ID. Sourced by nothing.

        The profile is about a *player*, not about this draft, so it licenses no
        champion name at all: its payload reports rank/LP/level and nothing that
        could be mistaken for a pick.
        """
        riot_id = as_str(args.get("riot_id")) or as_str(args.get("summoner"))
        if not riot_id:
            return ToolResult(
                "get_summoner_profile", False, {"error": "a riot_id like 'Name#TAG' is required"}
            )
        if self._live is None:
            return ToolResult(
                "get_summoner_profile",
                False,
                {"error": "live lookups are not configured"},
            )
        region = as_str(args.get("region"))
        profile = self._live.summoner_profile(riot_id, region)
        return ToolResult("get_summoner_profile", True, profile)

    def get_lane_matchup_guide(self, args: dict[str, object]) -> ToolResult:
        """A written tip for one specific lane matchup. Sources both champions."""
        champion = self._resolve_arg(args, "champion", "my_champion")
        opponent = self._resolve_arg(args, "opponent", "opponent_champion")
        role = as_str(args.get("role")) or as_str(args.get("position"))
        if champion is None or opponent is None:
            return ToolResult(
                "get_lane_matchup_guide",
                False,
                {
                    "error": "could not resolve both champions",
                    "unresolved": [
                        self._query(args, "champion", "my_champion"),
                        self._query(args, "opponent", "opponent_champion"),
                    ],
                },
            )
        if role not in LANES:
            return ToolResult(
                "get_lane_matchup_guide",
                False,
                {"error": "a lane is required", "role": role},
            )
        if self._live is None:
            return ToolResult(
                "get_lane_matchup_guide",
                False,
                {"error": "live lookups are not configured"},
            )
        guide = self._live.lane_matchup(
            _opgg_name(self._ontology, champion), _opgg_name(self._ontology, opponent), role
        )
        return ToolResult(
            "get_lane_matchup_guide",
            True,
            guide,
            sources=(champion, opponent),
        )

    def _query(self, args: dict[str, object], *keys: str) -> str:
        for key in keys:
            value = as_str(args.get(key))
            if value:
                return value
        return ""

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


def tool_specs(
    *,
    include_memory: bool = False,
    include_reference: bool = False,
    include_live: bool = False,
) -> list[dict[str, object]]:
    """Tool schemas in OpenAI function format (LangChain normalizes per provider).

    ``include_memory`` adds the session-memory tools (``recall_history``,
    ``remember``); they are only offered when the run is bound to a session.
    ``include_reference`` adds the materialized reference tools, and is set only
    when that data is actually loaded — advertised always equals available.
    ``include_live`` adds the two network tools, and is set only when a live
    provider is injected and the feature is enabled.
    """

    def fn(name: str, description: str, parameters: dict[str, object]) -> dict[str, object]:
        return {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": parameters},
        }

    names = {"type": "array", "items": {"type": "string"}}
    specs: list[dict[str, object]] = [
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
    if include_reference:
        specs.extend(_reference_specs(fn))
    if include_live:
        specs.extend(_live_specs(fn))
    if include_memory:
        specs.extend(memory_tool_specs())
    return specs


def _reference_specs(fn: _SpecFn) -> list[dict[str, object]]:
    """The materialized reference tools: tips, abilities, and builds.

    Both are evidence-only. Neither can name a pick, and their prose may mention
    other champions — which is exactly why their results carry ``sources``.
    """
    return [
        fn(
            "get_champion_tips",
            "Return a champion's tips (how to play with and against them), their "
            "abilities, and their lore. Reference material, not a recommendation.",
            {
                "type": "object",
                "properties": {"champion": {"type": "string"}},
                "required": ["champion"],
            },
        ),
        fn(
            "get_champion_build",
            "Return a champion's recommended items, runes, and skill order for a role. "
            "Reference material, not a pick recommendation.",
            {
                "type": "object",
                "properties": {
                    "champion": {"type": "string"},
                    "role": {"type": "string", "description": f"one of {list(LANES)}"},
                },
                "required": ["champion"],
            },
        ),
    ]


def _live_specs(fn: _SpecFn) -> list[dict[str, object]]:
    """The two tools that hit the network during a run.

    Both are evidence-only and neither can name a pick. The matchup guide's prose
    may mention champions, so its result declares ``sources`` (the pair); the
    profile is about a player, so it declares none.
    """
    return [
        fn(
            "get_summoner_profile",
            "Look up a player's current rank, LP, and win/loss record by Riot ID. "
            "Identity only - it says nothing about which champion to pick.",
            {
                "type": "object",
                "properties": {
                    "riot_id": {
                        "type": "string",
                        "description": "the player's Riot ID, e.g. 'Name#TAG'",
                    },
                    "region": {
                        "type": "string",
                        "description": "server region code, e.g. NA, EUW, KR; omit for the default",
                    },
                },
                "required": ["riot_id"],
            },
        ),
        fn(
            "get_lane_matchup_guide",
            "Return a written lane tip for one champion against one specific "
            "opponent in a role, with that champion's win rate in the lane. "
            "Evidence for a matchup the user named - not a pick recommendation.",
            {
                "type": "object",
                "properties": {
                    "champion": {"type": "string"},
                    "opponent": {"type": "string"},
                    "role": {"type": "string", "description": f"one of {list(LANES)}"},
                },
                "required": ["champion", "opponent", "role"],
            },
        ),
    ]
