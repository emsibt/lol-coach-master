"""The trace line — the interface between the engine and everything downstream.

Every point the engine adds to a score is emitted as a :class:`TraceLine` that
names its ``source`` (a knowledge-graph relation id) and the ``delta`` it
contributed. The narrator and the Trace Inspector consume these; nothing
downstream may assert a number that is not present here.
"""

from __future__ import annotations

from dataclasses import dataclass

from lolcoach.knowledge.ontology import Relation


@dataclass(frozen=True, slots=True)
class TraceLine:
    kind: str
    source: str
    text: str
    delta: float

    @classmethod
    def counter(cls, relation: Relation) -> TraceLine:
        """A counter contribution: ``relation.source`` beats ``relation.target``."""
        return cls(
            kind="counter",
            source=f"counter:{relation.source}->{relation.target}:{relation.position}",
            text=relation.reason,
            delta=relation.weight,
        )

    @classmethod
    def synergy(cls, relation: Relation) -> TraceLine:
        """A synergy contribution between ``relation.source`` and ``relation.target``."""
        pair = "~".join(sorted((relation.source, relation.target)))
        return cls(
            kind="synergy",
            source=f"synergy:{pair}:{relation.position}",
            text=relation.reason,
            delta=relation.weight,
        )
