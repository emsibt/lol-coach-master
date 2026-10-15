"""FastAPI app factory and routes.

The routes are thin: they translate HTTP to the workflow executor and the
execution store, and never make a drafting decision themselves. ``POST
/api/draft/evaluate`` runs the in-house draft-eval workflow (which owns every
persisted transition); the ``/api/executions*`` routes read that history back
for the Trace Inspector.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from lolcoach import __version__
from lolcoach.agent.llm import build_client
from lolcoach.agent.tools import normalize_board
from lolcoach.config import get_settings
from lolcoach.knowledge.ontology import Ontology, OntologyError, get_ontology
from lolcoach.server.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    BoardIn,
    ChampionOut,
    ChatRequest,
    ChatResponse,
    DraftRequest,
    EvaluateResponse,
    EventOut,
    ExecutionDetail,
    ExecutionSummary,
    PickOut,
    PositionOut,
    RerunResponse,
    ToolCallOut,
    TraceOut,
)
from lolcoach.values import as_str, opt_dict, opt_list
from lolcoach.workflow.executor import WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.workflows.agent_chat import AgentChat, build_agent_chat
from lolcoach.workflow.workflows.draft_eval import DraftEval, build_draft_eval

logger = logging.getLogger(__name__)


def _ontology() -> Ontology:
    try:
        return get_ontology()
    except OntologyError as exc:
        logger.error("Knowledge graph unavailable", extra={"data": {"error": str(exc)}})
        raise HTTPException(status_code=503, detail=f"knowledge graph unavailable: {exc}") from exc


def _build_workflow(
    name: str, ontology: Ontology, *, with_client: bool
) -> DraftEval | AgentChat | None:
    """Build a workflow by name. ``with_client`` gates the LLM (unused for topology)."""
    if name == "draft-eval":
        return build_draft_eval(ontology)
    if name == "agent-chat":
        settings = get_settings()
        client = build_client(settings) if with_client else None
        return build_agent_chat(ontology, client=client, settings=settings)
    return None


def _topology(name: str) -> dict[str, object] | None:
    """The static graph for a workflow, or ``None`` if it (or the KG) is unavailable."""
    try:
        built = _build_workflow(name, _ontology(), with_client=False)
    except HTTPException:
        return None
    return built.workflow.to_dict() if built is not None else None


def _store() -> Iterator[ExecutionStore]:
    store = ExecutionStore.connect(get_settings().executions_db)
    try:
        yield store
    finally:
        store.close()


def _pick(value: object) -> PickOut | None:
    data = opt_dict(value)
    return PickOut.model_validate(data) if data else None


def _traces(value: object) -> list[TraceOut]:
    return [TraceOut.model_validate(item) for item in opt_list(value)]


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title="LoL Coach", version=__version__)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service, "version": __version__}

    @app.get("/api/champions")
    def champions() -> list[ChampionOut]:
        ontology = _ontology()
        out: list[ChampionOut] = []
        for champion in sorted(ontology.champions.values(), key=lambda c: c.name):
            out.append(
                ChampionOut(
                    slug=champion.slug,
                    name=champion.name,
                    positions=[
                        PositionOut(
                            position=p.position,
                            win_rate=p.win_rate,
                            pick_rate=p.pick_rate,
                            ban_rate=p.ban_rate,
                            tier=p.tier,
                            roles=list(p.roles),
                        )
                        for p in champion.positions
                    ],
                )
            )
        return out

    @app.post("/api/draft/evaluate")
    def evaluate(
        request: DraftRequest,
        store: Annotated[ExecutionStore, Depends(_store)],
    ) -> EvaluateResponse:
        ontology = _ontology()
        built = _build_workflow("draft-eval", ontology, with_client=False)
        assert isinstance(built, DraftEval)
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        result = executor.run({"draft": request.model_dump()})

        output = result.output
        recommendation = opt_dict(output.get("recommendation"))
        return EvaluateResponse(
            execution_id=result.execution_id,
            status=result.status,
            error=result.error,
            problems=[as_str(item) for item in opt_list(output.get("problems"))],
            counter=_pick(recommendation.get("counter")),
            synergy=_pick(recommendation.get("synergy")),
            traces=_traces(output.get("traces")),
        )

    @app.get("/api/executions")
    def list_executions(
        store: Annotated[ExecutionStore, Depends(_store)],
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[ExecutionSummary]:
        return [ExecutionSummary.model_validate(row) for row in store.list_executions(limit)]

    @app.get("/api/executions/{execution_id}")
    def get_execution(
        execution_id: str, store: Annotated[ExecutionStore, Depends(_store)]
    ) -> ExecutionDetail:
        record = store.get_execution(execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no execution {execution_id!r}")
        events = [EventOut.model_validate(event) for event in store.events(execution_id)]
        payload = dict(record)
        payload["events"] = events
        payload["graph"] = _topology(as_str(record.get("workflow")))
        return ExecutionDetail.model_validate(payload)

    @app.post("/api/executions/{execution_id}/rerun")
    def rerun_execution(
        execution_id: str, store: Annotated[ExecutionStore, Depends(_store)]
    ) -> RerunResponse:
        record = store.get_execution(execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no execution {execution_id!r}")
        name = as_str(record.get("workflow"))
        initial = record.get("input")
        if not isinstance(initial, dict):
            raise HTTPException(status_code=400, detail="execution has no replayable input")
        built = _build_workflow(name, _ontology(), with_client=True)
        if built is None:
            raise HTTPException(status_code=400, detail=f"unknown workflow {name!r}")
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        result = executor.run(initial)
        return RerunResponse(
            execution_id=result.execution_id, status=result.status, error=result.error
        )

    @app.post("/api/chat")
    def chat(
        request: ChatRequest, store: Annotated[ExecutionStore, Depends(_store)]
    ) -> ChatResponse:
        record = store.get_execution(request.execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no execution {request.execution_id!r}")
        from lolcoach.agent.narrator import narrate

        output = opt_dict(record.get("output"))
        recommendation = opt_dict(output.get("recommendation"))
        narration = narrate(recommendation, question=request.message)
        return ChatResponse(reply=narration.text, mode=narration.mode)

    @app.post("/api/agent/chat")
    def agent_chat(
        request: AgentChatRequest, store: Annotated[ExecutionStore, Depends(_store)]
    ) -> AgentChatResponse:
        ontology = _ontology()
        settings = get_settings()
        client = build_client(settings)
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="no LLM is configured: set LOL_COACH_LLM_PROVIDER and its API key",
            )
        built = build_agent_chat(ontology, client=client, settings=settings)
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        result = executor.run(
            {
                "question": request.message,
                "board": request.board.model_dump(),
                "image": request.image.model_dump() if request.image is not None else None,
                "turns": [turn.model_dump() for turn in request.turns],
            }
        )

        output = result.output
        reply = as_str(output.get("reply"))
        fallback_mode = "failed" if result.status != "succeeded" else "offline"
        mode = as_str(output.get("mode")) or fallback_mode
        recommendation = opt_dict(output.get("recommendation"))
        return AgentChatResponse(
            execution_id=result.execution_id,
            status=result.status,
            reply=reply,
            mode=mode,
            board=BoardIn.model_validate(normalize_board(output.get("board"))),
            unresolved=[as_str(item) for item in opt_list(output.get("unresolved"))],
            tool_calls=[
                ToolCallOut(
                    name=as_str(opt_dict(item).get("name")),
                    ok=bool(opt_dict(item).get("ok")),
                    arguments=opt_dict(opt_dict(item).get("arguments")),
                )
                for item in opt_list(output.get("tool_calls"))
            ],
            counter=_pick(recommendation.get("counter")),
            synergy=_pick(recommendation.get("synergy")),
            traces=_traces(recommendation.get("traces")),
            error=result.error,
        )

    return app
