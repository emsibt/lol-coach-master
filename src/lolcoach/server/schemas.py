"""Pydantic request/response models for the HTTP API.

These mirror the engine's JSON output (see ``engine/explain.py``) and the
workflow's execution records (see ``workflow/store.py``). They are deliberately
thin — the engine stays the single source of truth for what a recommendation is.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lolcoach.constants import LANES


class DraftRequest(BaseModel):
    """A draft board submitted for evaluation."""

    allies: list[str] = Field(default_factory=list)
    enemies: list[str] = Field(default_factory=list)
    ally_bans: list[str] = Field(default_factory=list)
    enemy_bans: list[str] = Field(default_factory=list)
    open_roles: list[str] = Field(default_factory=lambda: list(LANES))


class TraceOut(BaseModel):
    kind: str
    source: str
    text: str
    delta: float


class PickOut(BaseModel):
    slug: str
    name: str
    role: str
    score: float
    traces: list[TraceOut] = Field(default_factory=list)


class EvaluateResponse(BaseModel):
    execution_id: str
    status: str
    error: str | None = None
    problems: list[str] = Field(default_factory=list)
    counter: PickOut | None = None
    synergy: PickOut | None = None
    traces: list[TraceOut] = Field(default_factory=list)


class PositionOut(BaseModel):
    position: str
    win_rate: float
    pick_rate: float
    ban_rate: float
    tier: int | None = None
    roles: list[str] = Field(default_factory=list)


class ChampionOut(BaseModel):
    slug: str
    name: str
    positions: list[PositionOut] = Field(default_factory=list)


class ExecutionSummary(BaseModel):
    execution_id: str
    workflow: str
    version: str
    status: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None


class EventOut(BaseModel):
    seq: int
    type: str
    state_name: str | None = None
    timestamp: str
    input: object | None = None
    output: object | None = None
    error: str | None = None
    traceback: str | None = None
    duration_ms: int | None = None
    logs: list[dict[str, object]] = Field(default_factory=list)


class ChoiceRuleOut(BaseModel):
    predicate: str
    next: str


class WorkflowStateOut(BaseModel):
    name: str
    type: str
    task: str | None = None
    next: str | None = None
    choices: list[ChoiceRuleOut] = Field(default_factory=list)
    default: str | None = None
    error: str | None = None


class WorkflowGraphOut(BaseModel):
    """The static workflow definition, so the Trace Inspector can draw the graph."""

    name: str
    version: str
    start_at: str
    states: list[WorkflowStateOut] = Field(default_factory=list)


class ExecutionDetail(BaseModel):
    execution_id: str
    workflow: str
    version: str
    status: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    input: object | None = None
    output: object | None = None
    events: list[EventOut] = Field(default_factory=list)
    graph: WorkflowGraphOut | None = None


class RerunResponse(BaseModel):
    execution_id: str
    status: str
    error: str | None = None


class ChatRequest(BaseModel):
    """Ask the narrator to explain an execution's recommendation."""

    execution_id: str
    message: str = ""


class ChatResponse(BaseModel):
    reply: str
    mode: str


class ImageIn(BaseModel):
    """An uploaded draft image, base64-encoded (vision input)."""

    media_type: str = "image/png"
    data_b64: str


class BoardIn(BaseModel):
    """The board state carried between chat turns (champion slugs)."""

    allies: list[str] = Field(default_factory=list)
    enemies: list[str] = Field(default_factory=list)
    ally_bans: list[str] = Field(default_factory=list)
    enemy_bans: list[str] = Field(default_factory=list)
    open_roles: list[str] = Field(default_factory=list)


class TurnIn(BaseModel):
    """A prior chat turn, for conversational context."""

    role: str
    text: str


class AgentChatRequest(BaseModel):
    """One message to the chat agent, plus the board state so far."""

    message: str = ""
    board: BoardIn = Field(default_factory=BoardIn)
    image: ImageIn | None = None
    turns: list[TurnIn] = Field(default_factory=list)


class ToolCallOut(BaseModel):
    name: str
    ok: bool
    arguments: dict[str, object] = Field(default_factory=dict)


class AgentChatResponse(BaseModel):
    execution_id: str
    status: str
    reply: str
    mode: str
    board: BoardIn
    unresolved: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    counter: PickOut | None = None
    synergy: PickOut | None = None
    traces: list[TraceOut] = Field(default_factory=list)
    error: str | None = None
