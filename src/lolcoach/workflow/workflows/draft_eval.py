"""The draft-evaluation pipeline expressed as a workflow definition.

    ValidateDraft -> IsValid(Choice) --invalid--> DraftInvalid(Fail)
                          | valid
                          v
    LoadKnowledge -> BuildCandidates -> ScoreCounters -> ScoreSynergies
        -> AssembleTrace -> PicksExist(Choice) --none--> NoPicks(Fail)
                          | picks
                          v
                       Succeeded

Task bodies are named registry entries that close over the loaded ontology, so
the definition stays inspectable and the workflow only ever carries JSON-safe
values between states.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from lolcoach.constants import LANES
from lolcoach.engine.draft_state import DraftState
from lolcoach.engine.explain import pick_to_dict
from lolcoach.engine.scoring import best_counter, best_synergy, candidate_slugs
from lolcoach.knowledge.ontology import Ontology
from lolcoach.values import as_str, opt_dict, opt_list
from lolcoach.workflow.types import (
    ChoiceRule,
    ChoiceState,
    FailState,
    State,
    SucceedState,
    TaskState,
    Workflow,
)

logger = logging.getLogger(__name__)

TaskFn = Callable[[dict[str, object]], dict[str, object]]
PredicateFn = Callable[[dict[str, object]], bool]


@dataclass(frozen=True, slots=True)
class DraftEval:
    workflow: Workflow
    tasks: dict[str, TaskFn]
    predicates: dict[str, PredicateFn]


def _slugs(value: object) -> tuple[str, ...]:
    return tuple(as_str(item) for item in opt_list(value))


def _state_to_dict(state: DraftState) -> dict[str, object]:
    return {
        "allies": list(state.allies),
        "enemies": list(state.enemies),
        "ally_bans": list(state.ally_bans),
        "enemy_bans": list(state.enemy_bans),
        "open_roles": list(state.open_roles),
    }


def _state_from(data: dict[str, object]) -> DraftState:
    raw = opt_dict(data.get("draft_state"))
    return DraftState(
        allies=_slugs(raw.get("allies")),
        enemies=_slugs(raw.get("enemies")),
        ally_bans=_slugs(raw.get("ally_bans")),
        enemy_bans=_slugs(raw.get("enemy_bans")),
        open_roles=_slugs(raw.get("open_roles")) or LANES,
    )


def build_draft_eval(ontology: Ontology) -> DraftEval:
    """Build the draft-eval workflow plus its task/predicate registries."""

    def validate_draft(data: dict[str, object]) -> dict[str, object]:
        raw = opt_dict(data.get("draft"))
        state = DraftState(
            allies=_slugs(raw.get("allies")),
            enemies=_slugs(raw.get("enemies")),
            ally_bans=_slugs(raw.get("ally_bans")),
            enemy_bans=_slugs(raw.get("enemy_bans")),
            open_roles=_slugs(raw.get("open_roles")) or LANES,
        )
        problems = state.problems()
        logger.info("Draft validated", extra={"data": {"problems": len(problems)}})
        return {"draft_state": _state_to_dict(state), "problems": problems}

    def load_knowledge(data: dict[str, object]) -> dict[str, object]:
        return {
            "patch": ontology.patch,
            "champion_count": len(ontology.champions),
            "relation_count": len(ontology.relations),
        }

    def build_candidates(data: dict[str, object]) -> dict[str, object]:
        slugs = candidate_slugs(ontology, _state_from(data))
        return {"candidates": slugs, "candidate_count": len(slugs)}

    def score_counters(data: dict[str, object]) -> dict[str, object]:
        pick = best_counter(ontology, _state_from(data))
        return {"counter": pick_to_dict(pick) if pick is not None else None}

    def score_synergies(data: dict[str, object]) -> dict[str, object]:
        counter = opt_dict(data.get("counter"))
        exclude = as_str(counter.get("slug")) or None
        pick = best_synergy(ontology, _state_from(data), exclude=exclude)
        return {"synergy": pick_to_dict(pick) if pick is not None else None}

    def assemble_trace(data: dict[str, object]) -> dict[str, object]:
        traces: list[object] = []
        for key in ("counter", "synergy"):
            pick = opt_dict(data.get(key))
            traces.extend(opt_list(pick.get("traces")))
        return {
            "recommendation": {"counter": data.get("counter"), "synergy": data.get("synergy")},
            "traces": traces,
        }

    def draft_is_valid(data: dict[str, object]) -> bool:
        return not opt_list(data.get("problems"))

    def has_picks(data: dict[str, object]) -> bool:
        return bool(data.get("counter")) or bool(data.get("synergy"))

    states: dict[str, State] = {
        "ValidateDraft": TaskState("ValidateDraft", "validate_draft", next="IsValid"),
        "IsValid": ChoiceState(
            "IsValid", (ChoiceRule("draft_is_valid", "LoadKnowledge"),), default="DraftInvalid"
        ),
        "DraftInvalid": FailState("DraftInvalid", error="draft failed validation"),
        "LoadKnowledge": TaskState("LoadKnowledge", "load_knowledge", next="BuildCandidates"),
        "BuildCandidates": TaskState("BuildCandidates", "build_candidates", next="ScoreCounters"),
        "ScoreCounters": TaskState("ScoreCounters", "score_counters", next="ScoreSynergies"),
        "ScoreSynergies": TaskState("ScoreSynergies", "score_synergies", next="AssembleTrace"),
        "AssembleTrace": TaskState("AssembleTrace", "assemble_trace", next="PicksExist"),
        "PicksExist": ChoiceState(
            "PicksExist", (ChoiceRule("has_picks", "Succeeded"),), default="NoPicks"
        ),
        "NoPicks": FailState("NoPicks", error="no counter or synergy pick available"),
        "Succeeded": SucceedState("Succeeded"),
    }

    workflow = Workflow(
        name="draft-eval",
        version="1",
        start_at="ValidateDraft",
        states=states,
    )
    tasks: dict[str, TaskFn] = {
        "validate_draft": validate_draft,
        "load_knowledge": load_knowledge,
        "build_candidates": build_candidates,
        "score_counters": score_counters,
        "score_synergies": score_synergies,
        "assemble_trace": assemble_trace,
    }
    predicates: dict[str, PredicateFn] = {
        "draft_is_valid": draft_is_valid,
        "has_picks": has_picks,
    }
    return DraftEval(workflow=workflow, tasks=tasks, predicates=predicates)
