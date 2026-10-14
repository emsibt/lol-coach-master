"""Tests for the in-house workflow executor and the draft-eval pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach.knowledge.ontology import Champion, Ontology, PositionInfo, Relation
from lolcoach.values import as_int
from lolcoach.workflow.events import (
    EXECUTION_FAILED,
    EXECUTION_STARTED,
    EXECUTION_SUCCEEDED,
    STATE_FAILED,
)
from lolcoach.workflow.executor import ExecutionResult, PredicateFn, TaskFn, WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.types import (
    ChoiceRule,
    ChoiceState,
    FailState,
    State,
    SucceedState,
    TaskState,
    Workflow,
    WorkflowError,
)
from lolcoach.workflow.workflows.draft_eval import build_draft_eval


def _store(tmp_path: Path) -> ExecutionStore:
    return ExecutionStore.connect(tmp_path / "executions.db")


def _simple_workflow() -> Workflow:
    states: dict[str, State] = {
        "Start": TaskState("Start", "double", next="Decide"),
        "Decide": ChoiceState("Decide", (ChoiceRule("is_positive", "Done"),), default="Bad"),
        "Bad": FailState("Bad", error="negative input"),
        "Done": SucceedState("Done"),
    }
    return Workflow(name="simple", version="1", start_at="Start", states=states)


def _simple_registries() -> tuple[dict[str, TaskFn], dict[str, PredicateFn]]:
    tasks: dict[str, TaskFn] = {"double": lambda data: {"n": as_int(data.get("n")) * 2}}
    predicates: dict[str, PredicateFn] = {"is_positive": lambda data: as_int(data.get("n")) > 0}
    return tasks, predicates


def _executor(store: ExecutionStore) -> WorkflowExecutor:
    tasks, predicates = _simple_registries()
    return WorkflowExecutor(_simple_workflow(), tasks=tasks, predicates=predicates, store=store)


# ---- definition validation -----------------------------------------------


def test_validate_rejects_unknown_next() -> None:
    states: dict[str, State] = {
        "Start": TaskState("Start", "noop", next="Nowhere"),
        "Done": SucceedState("Done"),
        "Bad": FailState("Bad", "no"),
    }
    with pytest.raises(WorkflowError):
        Workflow("w", "1", "Start", states).validate()


def test_validate_requires_terminal_states() -> None:
    states: dict[str, State] = {"Start": TaskState("Start", "noop", next=None)}
    with pytest.raises(WorkflowError):
        Workflow("w", "1", "Start", states).validate()


def test_executor_rejects_unregistered_task_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(WorkflowError):
        WorkflowExecutor(
            _simple_workflow(),
            tasks={"wrong_key": lambda data: {}},
            predicates={"is_positive": lambda data: True},
            store=store,
        )
    store.close()


# ---- execution -----------------------------------------------------------


def test_successful_run_is_gapless_and_ordered(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = _executor(store).run({"n": 5})

    assert result.status == "succeeded"
    assert result.output["n"] == 10
    assert [e.seq for e in result.events] == list(range(1, len(result.events) + 1))
    assert result.events[0].type == EXECUTION_STARTED
    assert result.events[-1].type == EXECUTION_SUCCEEDED

    listed = store.list_executions()
    assert listed[0]["execution_id"] == result.execution_id
    assert listed[0]["status"] == "succeeded"
    persisted = store.events(result.execution_id)
    assert [e["seq"] for e in persisted] == [e.seq for e in result.events]
    store.close()


def test_choice_routes_to_default_fail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = _executor(store).run({"n": 0})
    assert result.status == "failed"
    assert result.error == "negative input"
    assert any(e.type == STATE_FAILED and e.state_name == "Bad" for e in result.events)
    store.close()


def test_task_failure_records_traceback(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def boom(data: dict[str, object]) -> dict[str, object]:
        raise ValueError("kaboom")

    states: dict[str, State] = {
        "Start": TaskState("Start", "boom", next="Done"),
        "Done": SucceedState("Done"),
        "Bad": FailState("Bad", "x"),
    }
    workflow = Workflow("boom", "1", "Start", states)
    result = WorkflowExecutor(
        workflow, tasks={"boom": boom}, predicates={}, store=store
    ).run({})

    assert result.status == "failed"
    assert result.error == "ValueError: kaboom"
    failed = [e for e in result.events if e.type == STATE_FAILED]
    assert failed and failed[0].traceback is not None and "kaboom" in failed[0].traceback
    assert result.events[-1].type == EXECUTION_FAILED
    store.close()


# ---- draft-eval pipeline -------------------------------------------------


def _champion(slug: str) -> Champion:
    return Champion(
        slug=slug,
        name=slug.title(),
        champion_id=0,
        key=slug,
        positions=(
            PositionInfo("mid", 1000, 0.5, 0.01, 0.01, None, None, ()),
        ),
    )


def _relation(source: str, target: str, kind: str, weight: float) -> Relation:
    return Relation(source, target, kind, "mid", weight, 1.0, 1000, 0.5 + weight, "r")


def _ontology() -> Ontology:
    champions = [_champion("galio"), _champion("lux"), _champion("ryze"), _champion("soraka")]
    relations = [
        _relation("galio", "annie", "counter", 0.07),
        _relation("lux", "annie", "counter", 0.06),
        _relation("galio", "soraka", "synergy", 0.05),
        _relation("soraka", "ryze", "synergy", 0.04),
    ]
    counters: dict[str, list[Relation]] = {}
    synergies: dict[str, list[Relation]] = {}
    for relation in relations:
        (counters if relation.type == "counter" else synergies).setdefault(
            relation.source, []
        ).append(relation)
    return Ontology(
        patch="unit",
        generated_at="",
        source="unit",
        champions={c.slug: c for c in champions},
        relations=tuple(relations),
        counters_from={k: tuple(v) for k, v in counters.items()},
        synergies_with={k: tuple(v) for k, v in synergies.items()},
    )


def _run_draft(store: ExecutionStore, draft: dict[str, object]) -> ExecutionResult:
    built = build_draft_eval(_ontology())
    executor = WorkflowExecutor(
        built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
    )
    return executor.run({"draft": draft})


def test_draft_eval_produces_counter_and_synergy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = _run_draft(
        store, {"allies": ["soraka"], "enemies": ["annie"], "open_roles": ["mid"]}
    )
    assert result.status == "succeeded"
    recommendation = result.output["recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["counter"]["slug"] == "galio"
    assert recommendation["synergy"]["slug"] == "ryze"
    assert len(result.output["traces"]) == 2  # type: ignore[arg-type]

    state_names = [e.state_name for e in result.events if e.type == STATE_FAILED]
    assert state_names == []
    store.close()


def test_draft_eval_rejects_invalid_draft(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = _run_draft(
        store, {"allies": ["galio"], "enemy_bans": ["galio"], "open_roles": ["mid"]}
    )
    assert result.status == "failed"
    assert any(e.type == STATE_FAILED and e.state_name == "DraftInvalid" for e in result.events)
    store.close()


def test_draft_eval_fails_with_no_picks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = _run_draft(store, {"open_roles": ["mid"]})
    assert result.status == "failed"
    assert any(e.type == STATE_FAILED and e.state_name == "NoPicks" for e in result.events)
    store.close()
