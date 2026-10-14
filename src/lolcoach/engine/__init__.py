"""The deterministic scoring engine.

Pure and self-contained: no I/O, no network, no clock, and no imports from the
``dataprovider``, ``workflow``, ``server``, or ``agent`` layers. The engine takes
an :class:`~lolcoach.knowledge.ontology.Ontology` and a
:class:`~lolcoach.engine.draft_state.DraftState` and returns exactly one
counter-pick and one synergy-pick, each backed by source-tagged traces.

The product invariant lives here: *the LLM never decides picks* — this package
is what decides.
"""

from lolcoach.engine.draft_state import DraftState
from lolcoach.engine.scoring import (
    Pick,
    Recommendation,
    best_counter,
    best_synergy,
    candidate_slugs,
    evaluate_draft,
)
from lolcoach.engine.trace import TraceLine

__all__ = [
    "DraftState",
    "Pick",
    "Recommendation",
    "TraceLine",
    "best_counter",
    "best_synergy",
    "candidate_slugs",
    "evaluate_draft",
]
