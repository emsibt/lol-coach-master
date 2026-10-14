"""The in-house workflow engine.

A deliberately small, synchronous AWS-Step-Functions-style interpreter with an
immutable, write-through execution history. This layer is the **only** layer
that persists execution history.
"""

from lolcoach.workflow.events import Event
from lolcoach.workflow.executor import ExecutionResult, WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.types import (
    ChoiceRule,
    ChoiceState,
    FailState,
    SucceedState,
    TaskState,
    Workflow,
    WorkflowError,
)

__all__ = [
    "ChoiceRule",
    "ChoiceState",
    "Event",
    "ExecutionResult",
    "ExecutionStore",
    "FailState",
    "SucceedState",
    "TaskState",
    "Workflow",
    "WorkflowError",
    "WorkflowExecutor",
]
