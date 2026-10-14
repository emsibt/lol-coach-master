"""Counter and synergy scoring over the knowledge graph.

For a :class:`DraftState`, every champion that can still be picked is scored two
ways:

* **counter score** — the summed edge weight of "this champion beats an enemy
  pick" relations, in the role being filled;
* **synergy score** — the summed edge weight of "this champion pairs with an ally
  pick" relations.

Each contributing relation becomes a :class:`TraceLine`, so the final picks are
fully explained. The best counter-pick and the best synergy-pick are returned,
de-duplicated so the pair is actionable.
"""

from __future__ import annotations

from dataclasses import dataclass

from lolcoach.constants import LANES
from lolcoach.engine.draft_state import DraftState
from lolcoach.engine.trace import TraceLine
from lolcoach.knowledge.ontology import Champion, Ontology, Relation

_ROLE_ORDER = {role: index for index, role in enumerate(LANES)}


@dataclass(frozen=True, slots=True)
class Pick:
    slug: str
    name: str
    role: str
    score: float
    traces: tuple[TraceLine, ...]


@dataclass(frozen=True, slots=True)
class Recommendation:
    counter: Pick | None
    synergy: Pick | None

    def traces(self) -> tuple[TraceLine, ...]:
        lines: list[TraceLine] = []
        for pick in (self.counter, self.synergy):
            if pick is not None:
                lines.extend(pick.traces)
        return tuple(lines)


def evaluate_draft(ontology: Ontology, state: DraftState) -> Recommendation:
    """Return exactly one counter-pick and one synergy-pick for ``state``.

    Either may be ``None`` when there is no opposing/ally pick to score against,
    or when no candidate has a positive contribution.
    """
    counter = best_counter(ontology, state)
    exclude = counter.slug if counter is not None else None
    synergy = best_synergy(ontology, state, exclude=exclude)
    return Recommendation(counter=counter, synergy=synergy)


def best_counter(ontology: Ontology, state: DraftState) -> Pick | None:
    """The strongest available counter-pick against the enemy team."""
    return _best(ontology, state, kind="counter", exclude=None)


def best_synergy(
    ontology: Ontology, state: DraftState, *, exclude: str | None = None
) -> Pick | None:
    """The strongest available synergy-pick with our team, skipping ``exclude``."""
    return _best(ontology, state, kind="synergy", exclude=exclude)


def candidate_slugs(ontology: Ontology, state: DraftState) -> list[str]:
    """Every champion that could still be picked, sorted by slug (deterministic)."""
    return [champion.slug for champion in _candidates(ontology, state)]


def _candidates(ontology: Ontology, state: DraftState) -> list[Champion]:
    unavailable = state.unavailable()
    available = [
        champion
        for champion in ontology.champions.values()
        if champion.slug not in unavailable and _open_roles(champion, state.open_roles)
    ]
    return sorted(available, key=lambda c: c.slug)


def _open_roles(champion: Champion, open_roles: tuple[str, ...]) -> list[str]:
    roles = [p.position for p in champion.positions if p.position in open_roles]
    return sorted(set(roles), key=lambda role: _ROLE_ORDER[role])


def _best(
    ontology: Ontology, state: DraftState, *, kind: str, exclude: str | None
) -> Pick | None:
    best: Pick | None = None
    for champion in _candidates(ontology, state):
        if champion.slug == exclude:
            continue
        for role in _open_roles(champion, state.open_roles):
            pick = _score(ontology, state, champion, role, kind)
            if pick is not None and _better(pick, best):
                best = pick
    return best


def _better(pick: Pick, best: Pick | None) -> bool:
    if best is None:
        return True
    if pick.score != best.score:
        return pick.score > best.score
    if pick.slug != best.slug:
        return pick.slug < best.slug
    return _ROLE_ORDER[pick.role] < _ROLE_ORDER[best.role]


def _score(
    ontology: Ontology, state: DraftState, champion: Champion, role: str, kind: str
) -> Pick | None:
    counterparts = state.enemies if kind == "counter" else state.allies
    if not counterparts:
        return None

    total = 0.0
    traces: list[TraceLine] = []
    for other in counterparts:
        relation = (
            _counter_between(ontology, champion.slug, other, role)
            if kind == "counter"
            else _synergy_between(ontology, champion.slug, other, role)
        )
        if relation is None:
            continue
        total += relation.weight
        traces.append(
            TraceLine.counter(relation) if kind == "counter" else TraceLine.synergy(relation)
        )

    if total <= 0.0 or not traces:
        return None
    return Pick(
        slug=champion.slug,
        name=champion.name,
        role=role,
        score=total,
        traces=tuple(traces),
    )


def _counter_between(
    ontology: Ontology, attacker: str, defender: str, role: str
) -> Relation | None:
    """The strongest counter edge where ``attacker`` beats ``defender`` in ``role``."""
    best: Relation | None = None
    for relation in ontology.counters_from.get(attacker, ()):
        if relation.target == defender and relation.position == role:
            if best is None or relation.weight > best.weight:
                best = relation
    return best


def _synergy_between(ontology: Ontology, first: str, second: str, role: str) -> Relation | None:
    """The strongest synergy edge between two champions in ``role`` (either direction)."""
    best: Relation | None = None
    for anchor, other in ((first, second), (second, first)):
        for relation in ontology.synergies_with.get(anchor, ()):
            if relation.target == other and relation.position == role:
                if best is None or relation.weight > best.weight:
                    best = relation
    return best
