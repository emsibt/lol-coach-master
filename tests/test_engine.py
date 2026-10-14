"""Tests for the pure deterministic engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach.constants import LANES
from lolcoach.engine.draft_state import DraftState
from lolcoach.engine.explain import describe, recommendation_to_dict
from lolcoach.engine.scoring import evaluate_draft
from lolcoach.knowledge.ontology import Champion, Ontology, PositionInfo, Relation

_ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "lolcoach" / "engine"
_FORBIDDEN = ("lolcoach.dataprovider", "lolcoach.workflow", "lolcoach.server", "lolcoach.agent")


def _champion(slug: str, roles: tuple[str, ...] = ("mid",)) -> Champion:
    return Champion(
        slug=slug,
        name=slug.title(),
        champion_id=0,
        key=slug,
        positions=tuple(
            PositionInfo(
                position=r,
                play=1000,
                win_rate=0.5,
                pick_rate=0.01,
                ban_rate=0.01,
                tier=None,
                rank=None,
                roles=(),
            )
            for r in roles
        ),
    )


def _relation(
    source: str, target: str, kind: str, position: str, weight: float, reason: str = "r"
) -> Relation:
    return Relation(
        source=source,
        target=target,
        type=kind,
        position=position,
        weight=weight,
        confidence=1.0,
        games=1000,
        win_rate=0.5 + weight,
        reason=reason,
    )


def _ontology(champions: list[Champion], relations: list[Relation]) -> Ontology:
    counters: dict[str, list[Relation]] = {}
    synergies: dict[str, list[Relation]] = {}
    for relation in relations:
        bucket = counters if relation.type == "counter" else synergies
        bucket.setdefault(relation.source, []).append(relation)
    return Ontology(
        patch="unit",
        generated_at="",
        source="unit",
        champions={c.slug: c for c in champions},
        relations=tuple(relations),
        counters_from={k: tuple(v) for k, v in counters.items()},
        synergies_with={k: tuple(v) for k, v in synergies.items()},
    )


def _board(
    *,
    allies: tuple[str, ...] = (),
    enemies: tuple[str, ...] = (),
    ally_bans: tuple[str, ...] = (),
    enemy_bans: tuple[str, ...] = (),
    open_roles: tuple[str, ...] = LANES,
) -> DraftState:
    return DraftState(
        allies=allies,
        enemies=enemies,
        ally_bans=ally_bans,
        enemy_bans=enemy_bans,
        open_roles=open_roles,
    )


# ---- picks ---------------------------------------------------------------


def test_counter_pick_is_strongest_counter_against_enemy() -> None:
    champions = [_champion("galio"), _champion("lux"), _champion("ryze")]
    relations = [
        _relation("galio", "annie", "counter", "mid", 0.07, "galio counters annie"),
        _relation("lux", "annie", "counter", "mid", 0.06),
    ]
    board = _board(enemies=("annie",), open_roles=("mid",))
    result = evaluate_draft(_ontology(champions, relations), board)
    assert result.counter is not None
    assert result.counter.name == "Galio"
    assert result.counter.role == "mid"
    assert result.counter.traces[0].text == "galio counters annie"
    assert result.counter.traces[0].delta == pytest.approx(0.07)


def test_synergy_pick_matches_either_relation_direction() -> None:
    champions = [_champion("galio"), _champion("ryze"), _champion("soraka")]
    relations = [
        _relation("soraka", "ryze", "synergy", "mid", 0.04, "soraka + ryze"),
    ]
    state = _board(allies=("soraka",), open_roles=("mid",))
    result = evaluate_draft(_ontology(champions, relations), state)
    assert result.synergy is not None
    assert result.synergy.name == "Ryze"
    assert result.synergy.traces[0].text == "soraka + ryze"


def test_picks_are_deduplicated() -> None:
    champions = [_champion("galio"), _champion("lux"), _champion("ryze"), _champion("soraka")]
    relations = [
        _relation("galio", "annie", "counter", "mid", 0.07),
        _relation("lux", "annie", "counter", "mid", 0.06),
        _relation("galio", "soraka", "synergy", "mid", 0.05),
        _relation("soraka", "ryze", "synergy", "mid", 0.04),
    ]
    state = _board(allies=("soraka",), enemies=("annie",), open_roles=("mid",))
    result = evaluate_draft(_ontology(champions, relations), state)
    assert result.counter is not None and result.counter.slug == "galio"
    assert result.synergy is not None and result.synergy.slug == "ryze"


def test_closed_roles_exclude_candidates() -> None:
    champions = [_champion("darius", roles=("top",))]
    relations = [_relation("darius", "annie", "counter", "top", 0.09)]
    state = _board(enemies=("annie",), open_roles=("mid",))
    assert evaluate_draft(_ontology(champions, relations), state).counter is None


def test_unavailable_champions_are_excluded() -> None:
    champions = [_champion("galio"), _champion("lux")]
    relations = [
        _relation("galio", "annie", "counter", "mid", 0.07),
        _relation("lux", "annie", "counter", "mid", 0.06),
    ]
    state = _board(enemies=("annie",), enemy_bans=("galio",), open_roles=("mid",))
    result = evaluate_draft(_ontology(champions, relations), state)
    assert result.counter is not None and result.counter.slug == "lux"


def test_ties_break_deterministically_by_slug() -> None:
    champions = [_champion("zoe"), _champion("ahri")]
    relations = [
        _relation("zoe", "annie", "counter", "mid", 0.05),
        _relation("ahri", "annie", "counter", "mid", 0.05),
    ]
    board = _board(enemies=("annie",), open_roles=("mid",))
    result = evaluate_draft(_ontology(champions, relations), board)
    assert result.counter is not None and result.counter.slug == "ahri"


def test_no_enemies_yields_no_counter_pick() -> None:
    champions = [_champion("galio")]
    result = evaluate_draft(_ontology(champions, []), _board(open_roles=("mid",)))
    assert result.counter is None


def test_recommendation_serializes_with_traces() -> None:
    champions = [_champion("galio")]
    relations = [_relation("galio", "annie", "counter", "mid", 0.07, "reason text")]
    state = _board(enemies=("annie",), open_roles=("mid",))
    payload = recommendation_to_dict(evaluate_draft(_ontology(champions, relations), state))
    counter = payload["counter"]
    assert isinstance(counter, dict)
    assert counter["slug"] == "galio"
    assert counter["traces"][0]["text"] == "reason text"
    assert "Galio" in describe(evaluate_draft(_ontology(champions, relations), state))


# ---- draft-state validation ---------------------------------------------


def test_board_rejects_duplicate_champion() -> None:
    state = _board(allies=("galio",), enemy_bans=("galio",), open_roles=("mid",))
    assert not state.is_valid()
    assert any("galio" in p for p in state.problems())


def test_board_rejects_unknown_role() -> None:
    state = _board(open_roles=("mid", "bush"))
    assert not state.is_valid()


def test_board_accepts_a_normal_state() -> None:
    state = _board(allies=("jinx",), enemies=("annie",), open_roles=("mid", "support"))
    assert state.is_valid()


# ---- purity boundary -----------------------------------------------------


def test_engine_does_not_import_other_layers() -> None:
    for module in _ENGINE_DIR.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN:
            assert forbidden not in source, f"{module.name} imports {forbidden}"
