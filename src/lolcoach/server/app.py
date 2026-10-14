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
from lolcoach.agent.agent import step_from_dict
from lolcoach.agent.llm import build_client
from lolcoach.agent.narrator import offline_final_text
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
    ToolCallOut,
    TraceOut,
)
from lolcoach.values import as_str, opt_dict, opt_list
from lolcoach.workflow.executor import WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.workflows.agent_chat import build_agent_chat
from lolcoach.workflow.workflows.draft_eval import build_draft_eval

logger = logging.getLogger(__name__)


def _ontology() -> Ontology:
    try:
        return get_ontology()
    except OntologyError as exc:
        logger.error("Knowledge graph unavailable", extra={"data": {"error": str(exc)}})
        raise HTTPException(status_code=503, detail=f"knowledge graph unavailable: {exc}") from exc


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
        built = build_draft_eval(ontology)
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
        return ExecutionDetail.model_validate(payload)

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
        client = build_client(settings)
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
        steps = [step_from_dict(item) for item in opt_list(output.get("history"))]
        reply = as_str(output.get("reply"))
        mode = as_str(output.get("mode"), "offline")
        if not reply:
            reply = offline_final_text(steps, ontology)
            mode = "offline"
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
