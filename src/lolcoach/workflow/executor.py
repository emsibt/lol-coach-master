"""The in-house AWS-Step-Functions-style executor.

Deliberately not a framework: a small, synchronous interpreter over a validated
:class:`~lolcoach.workflow.types.Workflow`. Task bodies are **named registry
keys**, not inline closures, so definitions stay auditable, and every transition
is written through to the execution store as it happens.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from lolcoach.workflow.events import (
    EXECUTION_FAILED,
    EXECUTION_STARTED,
    EXECUTION_SUCCEEDED,
    STATE_ENTERED,
    STATE_FAILED,
    STATE_SUCCEEDED,
    Event,
)
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.types import (
    ChoiceState,
    FailState,
    State,
    SucceedState,
    TaskState,
    Workflow,
    WorkflowError,
)

logger = logging.getLogger(__name__)

TaskFn = Callable[[dict[str, object]], dict[str, object]]
PredicateFn = Callable[[dict[str, object]], bool]
Emitter = Callable[..., Event]

_MAX_STEPS = 1000


class _Abort(Exception):
    """Internal signal that a state failed; carries the message to record."""

    def __init__(self, state_name: str | None, error: str) -> None:
        super().__init__(error)
        self.state_name = state_name
        self.error = error


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    execution_id: str
    status: str
    output: dict[str, object]
    error: str | None
    events: tuple[Event, ...]


def _jsonable(value: object | None) -> object | None:
    """Best-effort JSON view of a value, for persisting in an event."""
    if value is None:
        return None
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return cast(object, json.loads(json.dumps(value, default=str)))
    return value


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class WorkflowExecutor:
    def __init__(
        self,
        workflow: Workflow,
        *,
        tasks: Mapping[str, TaskFn],
        predicates: Mapping[str, PredicateFn],
        store: ExecutionStore,
        now: Callable[[], str] = _utc_now_iso,
        monotonic: Callable[[], float] = time.monotonic,
        max_steps: int = _MAX_STEPS,
    ) -> None:
        workflow.validate()
        for key in workflow.task_keys():
            if key not in tasks:
                raise WorkflowError(f"no task registered for key {key!r}")
        for key in workflow.predicate_keys():
            if key not in predicates:
                raise WorkflowError(f"no predicate registered for key {key!r}")
        self._workflow = workflow
        self._tasks = tasks
        self._predicates = predicates
        self._store = store
        self._now = now
        self._monotonic = monotonic
        self._max_steps = max_steps

    def run(self, initial: Mapping[str, object] | None = None) -> ExecutionResult:
        """Execute the workflow, writing every event through to the store."""
        data: dict[str, object] = dict(initial or {})
        execution_id = uuid4().hex
        events: list[Event] = []
        seq = 0

        def emit(
            type: str,
            state_name: str | None = None,
            *,
            input: object = None,
            output: object = None,
            error: str | None = None,
            tb: str | None = None,
            duration_ms: int | None = None,
        ) -> Event:
            nonlocal seq
            seq += 1
            event = Event(
                execution_id=execution_id,
                seq=seq,
                type=type,
                state_name=state_name,
                timestamp=self._now(),
                input=_jsonable(input),
                output=_jsonable(output),
                error=error,
                traceback=tb,
                duration_ms=duration_ms,
            )
            events.append(event)
            self._store.append(event)
            return event

        self._store.create_execution(
            execution_id,
            self._workflow.name,
            self._workflow.version,
            self._now(),
            _jsonable(data),
        )
        emit(EXECUTION_STARTED, input=data)

        status = "failed"
        error: str | None = None
        try:
            current = self._workflow.start_at
            for _ in range(self._max_steps):
                state: State = self._workflow.states[current]
                if isinstance(state, SucceedState):
                    emit(STATE_SUCCEEDED, state.name, output=data)
                    status, error = "succeeded", None
                    break
                if isinstance(state, FailState):
                    emit(STATE_FAILED, state.name, error=state.error)
                    status, error = "failed", state.error
                    break
                emit(STATE_ENTERED, state.name)
                if isinstance(state, TaskState):
                    self._run_task(state, data, emit)
                    if state.next is None:
                        status, error = "succeeded", None
                        break
                    current = state.next
                elif isinstance(state, ChoiceState):
                    current = self._run_choice(state, data, emit)
                else:  # pragma: no cover - exhaustive union
                    raise _Abort(state.name, "unknown state type")
            else:
                raise _Abort(None, "exceeded maximum step count")
        except _Abort as abort:
            status, error = "failed", abort.error

        final_output = _jsonable(data)
        if status == "succeeded":
            emit(EXECUTION_SUCCEEDED, output=data)
        else:
            emit(EXECUTION_FAILED, error=error)
        self._store.finish(execution_id, status, self._now(), final_output, error)
        logger.info(
            "Workflow execution finished",
            extra={
                "data": {
                    "execution_id": execution_id,
                    "workflow": self._workflow.name,
                    "status": status,
                }
            },
        )
        return ExecutionResult(
            execution_id=execution_id,
            status=status,
            output=data,
            error=error,
            events=tuple(events),
        )

    def _run_task(self, state: TaskState, data: dict[str, object], emit: Emitter) -> None:
        task = self._tasks[state.task]
        started = self._monotonic()
        try:
            result = task(dict(data))
        except Exception as exc:
            tb = traceback.format_exc()
            emit(
                STATE_FAILED,
                state.name,
                error=f"{type(exc).__name__}: {exc}",
                tb=tb,
                duration_ms=self._elapsed(started),
            )
            logger.exception(
                "Workflow task failed", extra={"data": {"state": state.name, "task": state.task}}
            )
            raise _Abort(state.name, f"{type(exc).__name__}: {exc}") from exc
        data.update(result)
        emit(STATE_SUCCEEDED, state.name, output=result, duration_ms=self._elapsed(started))

    def _run_choice(self, state: ChoiceState, data: dict[str, object], emit: Emitter) -> str:
        started = self._monotonic()
        try:
            chosen: str | None = None
            for rule in state.choices:
                if self._predicates[rule.predicate](dict(data)):
                    chosen = rule.next
                    break
            if chosen is None:
                chosen = state.default
        except Exception as exc:
            tb = traceback.format_exc()
            emit(
                STATE_FAILED,
                state.name,
                error=f"{type(exc).__name__}: {exc}",
                tb=tb,
                duration_ms=self._elapsed(started),
            )
            logger.exception("Workflow predicate failed", extra={"data": {"state": state.name}})
            raise _Abort(state.name, f"{type(exc).__name__}: {exc}") from exc

        if chosen is None:
            emit(
                STATE_FAILED,
                state.name,
                error="no choice rule matched and no default",
                duration_ms=self._elapsed(started),
            )
            raise _Abort(state.name, "no choice rule matched and no default")

        emit(
            STATE_SUCCEEDED,
            state.name,
            output={"next": chosen},
            duration_ms=self._elapsed(started),
        )
        return chosen

    def _elapsed(self, started: float) -> int:
        return int((self._monotonic() - started) * 1000)
