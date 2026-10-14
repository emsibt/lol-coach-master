"""Immutable execution events — the unit of the audit trail."""

from __future__ import annotations

from dataclasses import dataclass

EXECUTION_STARTED = "EXECUTION_STARTED"
STATE_ENTERED = "STATE_ENTERED"
STATE_SUCCEEDED = "STATE_SUCCEEDED"
STATE_FAILED = "STATE_FAILED"
EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass(frozen=True, slots=True)
class Event:
    execution_id: str
    seq: int
    type: str
    state_name: str | None
    timestamp: str
    input: object | None = None
    output: object | None = None
    error: str | None = None
    traceback: str | None = None
    duration_ms: int | None = None
