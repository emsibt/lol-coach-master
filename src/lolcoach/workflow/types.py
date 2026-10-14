"""The in-house Step-Functions-style workflow definition types.

A :class:`Workflow` is a frozen graph of named states. Validation happens at
construction time so a mis-wired definition (unknown ``next``, missing terminal,
unregistered task key) fails before any execution runs.
"""

from __future__ import annotations

from dataclasses import dataclass


class WorkflowError(RuntimeError):
    """Raised when a workflow definition or its registries are invalid."""


@dataclass(frozen=True, slots=True)
class TaskState:
    """Run the task registered under ``task``, then go to ``next`` (or end)."""

    name: str
    task: str
    next: str | None = None


@dataclass(frozen=True, slots=True)
class ChoiceRule:
    predicate: str
    next: str


@dataclass(frozen=True, slots=True)
class ChoiceState:
    """Take the first rule whose predicate is true, else ``default``."""

    name: str
    choices: tuple[ChoiceRule, ...]
    default: str | None = None


@dataclass(frozen=True, slots=True)
class SucceedState:
    name: str


@dataclass(frozen=True, slots=True)
class FailState:
    name: str
    error: str
    cause: str = ""


State = TaskState | ChoiceState | SucceedState | FailState


@dataclass(frozen=True, slots=True)
class Workflow:
    name: str
    version: str
    start_at: str
    states: dict[str, State]

    def validate(self) -> None:
        """Raise :class:`WorkflowError` if the graph is malformed."""
        problems: list[str] = []
        states = self.states

        if not states:
            problems.append("workflow has no states")
        if self.start_at not in states:
            problems.append(f"start_at {self.start_at!r} is not a state")

        for key, state in states.items():
            if key != state.name:
                problems.append(f"state key {key!r} does not match name {state.name!r}")
            if isinstance(state, TaskState) and state.next is not None:
                if state.next not in states:
                    problems.append(f"{key}: next {state.next!r} is not a state")
            elif isinstance(state, ChoiceState):
                if not state.choices:
                    problems.append(f"{key}: choice has no rules")
                for rule in state.choices:
                    if rule.next not in states:
                        problems.append(f"{key}: rule next {rule.next!r} is not a state")
                if state.default is not None and state.default not in states:
                    problems.append(f"{key}: default {state.default!r} is not a state")

        if not any(isinstance(s, SucceedState) for s in states.values()):
            problems.append("workflow has no Succeed state")
        if not any(isinstance(s, FailState) for s in states.values()):
            problems.append("workflow has no Fail state")

        if problems:
            raise WorkflowError("invalid workflow: " + "; ".join(problems))

    def task_keys(self) -> list[str]:
        return [s.task for s in self.states.values() if isinstance(s, TaskState)]

    def predicate_keys(self) -> list[str]:
        return [
            rule.predicate
            for s in self.states.values()
            if isinstance(s, ChoiceState)
            for rule in s.choices
        ]
