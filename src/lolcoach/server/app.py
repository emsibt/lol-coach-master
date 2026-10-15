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
from lolcoach.agent.tools import LiveDataProvider, normalize_board
from lolcoach.chat.store import ChatStore, Message, Session, SessionLimitReached
from lolcoach.config import Settings, get_settings
from lolcoach.knowledge.ontology import Ontology, OntologyError, get_ontology
from lolcoach.knowledge.reference import get_reference
from lolcoach.server.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    BoardIn,
    ChampionOut,
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    DraftRequest,
    EvaluateResponse,
    EventOut,
    ExecutionDetail,
    ExecutionSummary,
    HealthResponse,
    PickOut,
    PositionOut,
    RenameSessionRequest,
    RerunResponse,
    SessionDetail,
    SessionMessageOut,
    SessionSummary,
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


def _live(settings: Settings) -> LiveDataProvider | None:
    """The live provider, or ``None`` when the feature is switched off.

    Imported here rather than at module scope so the server does not drag the
    network layer in until the composition root actually needs it.
    """
    if not settings.enable_live_tools:
        return None
    from lolcoach.dataprovider.live import build_live_provider

    return build_live_provider()


def _build_workflow(
    name: str, ontology: Ontology, *, with_client: bool
) -> DraftEval | AgentChat | None:
    """Build a workflow by name. ``with_client`` gates the LLM (unused for topology)."""
    if name == "draft-eval":
        return build_draft_eval(ontology)
    if name == "agent-chat":
        settings = get_settings()
        client = build_client(settings) if with_client else None
        return build_agent_chat(
            ontology,
            client=client,
            settings=settings,
            reference=get_reference(),
            live=_live(settings),
        )
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


def _chat_store() -> Iterator[ChatStore]:
    store = ChatStore.connect(get_settings().chat_db)
    try:
        yield store
    finally:
        store.close()


_BOARD_CONTENT_KEYS = ("allies", "enemies", "ally_bans", "enemy_bans")


def _board_in(value: object) -> BoardIn:
    return BoardIn.model_validate(normalize_board(value))


def _board_has_content(board: BoardIn) -> bool:
    return any(getattr(board, key) for key in _BOARD_CONTENT_KEYS)


def _session_summary(session: Session) -> SessionSummary:
    return SessionSummary(
        session_id=session.session_id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=session.message_count,
        question_count=session.question_count,
        max_questions=session.max_questions,
        remaining_questions=session.remaining_questions,
    )


def _tool_calls(value: object) -> list[ToolCallOut]:
    return [
        ToolCallOut(
            name=as_str(opt_dict(item).get("name")),
            ok=bool(opt_dict(item).get("ok")),
            arguments=opt_dict(opt_dict(item).get("arguments")),
        )
        for item in opt_list(value)
    ]


def _session_message(message: Message) -> SessionMessageOut:
    meta = message.meta
    if message.role != "assistant":
        return SessionMessageOut(
            seq=message.seq, role=message.role, text=message.text, created_at=message.created_at
        )
    return SessionMessageOut(
        seq=message.seq,
        role=message.role,
        text=message.text,
        created_at=message.created_at,
        mode=as_str(meta.get("mode")) or None,
        tool_calls=_tool_calls(meta.get("tool_calls")),
        counter=_pick(meta.get("counter")),
        synergy=_pick(meta.get("synergy")),
        execution_id=as_str(meta.get("execution_id")) or None,
        error=as_str(meta.get("error")) or None,
    )


def _session_detail(chats: ChatStore, session: Session) -> SessionDetail:
    return SessionDetail(
        **_session_summary(session).model_dump(),
        board=_board_in(session.board),
        notes=chats.notes(session.session_id),
        messages=[_session_message(message) for message in chats.messages(session.session_id)],
    )


def _persist_turn(
    chats: ChatStore,
    session: Session,
    output: dict[str, object],
    response: AgentChatResponse,
) -> AgentChatResponse:
    """Write the completed turn back to the session and attach its counters."""
    known = chats.notes(session.session_id)
    for note in (as_str(item).strip() for item in opt_list(output.get("notes"))):
        if note and note not in known:
            chats.add_note(session.session_id, note)

    text = response.reply or response.error or "The coach did not produce an answer."
    chats.append_message(
        session.session_id,
        "assistant",
        text,
        meta={
            "mode": response.mode,
            "tool_calls": [call.model_dump() for call in response.tool_calls],
            "counter": response.counter.model_dump() if response.counter else None,
            "synergy": response.synergy.model_dump() if response.synergy else None,
            "execution_id": response.execution_id,
            "error": response.error,
        },
    )
    chats.touch_session(session.session_id, board=output.get("board"))

    refreshed = chats.get_session(session.session_id) or session
    return response.model_copy(
        update={
            "session_id": refreshed.session_id,
            "question_count": refreshed.question_count,
            "max_questions": refreshed.max_questions,
            "remaining_questions": refreshed.remaining_questions,
        }
    )


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
    def health() -> HealthResponse:
        try:
            ontology = get_ontology()
            patch, ddragon = ontology.patch, ontology.ddragon_version
        except OntologyError:
            patch, ddragon = "", ""
        return HealthResponse(
            status="ok",
            service=settings.service,
            version=__version__,
            patch=patch,
            ddragon=ddragon,
        )

    @app.get("/api/champions")
    def champions() -> list[ChampionOut]:
        ontology = _ontology()
        out: list[ChampionOut] = []
        for champion in sorted(ontology.champions.values(), key=lambda c: c.name):
            out.append(
                ChampionOut(
                    slug=champion.slug,
                    name=champion.name,
                    key=champion.key,
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
        request: AgentChatRequest,
        store: Annotated[ExecutionStore, Depends(_store)],
        chats: Annotated[ChatStore, Depends(_chat_store)],
    ) -> AgentChatResponse:
        ontology = _ontology()
        settings = get_settings()

        session: Session | None = None
        if request.session_id is not None:
            session = chats.get_session(request.session_id)
            if session is None:
                raise HTTPException(
                    status_code=404, detail=f"no session {request.session_id!r}"
                )
            if session.question_count >= session.max_questions:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"session reached its limit of {session.max_questions} questions; "
                        "start a new chat"
                    ),
                )

        client = build_client(settings)
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="no LLM is configured: set LOL_COACH_LLM_PROVIDER and its API key",
            )

        question = request.message
        image = request.image.model_dump() if request.image is not None else None

        if session is not None:
            board = (
                request.board
                if _board_has_content(request.board)
                else _board_in(session.board)
            )
            turns: list[dict[str, object]] = [
                {"role": message.role, "text": message.text}
                for message in chats.messages(session.session_id)
                if message.role in ("user", "assistant") and message.text.strip()
            ]
            notes = chats.notes(session.session_id)
            # The question consumes a slot even if the run later fails, so the cap
            # bounds requests, not answers.
            try:
                chats.append_question(session.session_id, question or "(draft image)")
            except SessionLimitReached as exc:  # pragma: no cover - guarded above
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            board = request.board
            turns = [turn.model_dump() for turn in request.turns]
            notes = []

        built = build_agent_chat(
            ontology,
            client=client,
            settings=settings,
            reference=get_reference(),
            live=_live(settings),
        )
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        result = executor.run(
            {
                "question": question,
                "board": board.model_dump(),
                "image": image,
                "turns": turns,
                "notes": notes,
                "memory": session is not None,
            }
        )

        output = result.output
        reply = as_str(output.get("reply"))
        fallback_mode = "failed" if result.status != "succeeded" else "offline"
        mode = as_str(output.get("mode")) or fallback_mode
        recommendation = opt_dict(output.get("recommendation"))
        response = AgentChatResponse(
            execution_id=result.execution_id,
            status=result.status,
            reply=reply,
            mode=mode,
            board=_board_in(output.get("board")),
            unresolved=[as_str(item) for item in opt_list(output.get("unresolved"))],
            tool_calls=_tool_calls(output.get("tool_calls")),
            counter=_pick(recommendation.get("counter")),
            synergy=_pick(recommendation.get("synergy")),
            traces=_traces(recommendation.get("traces")),
            error=result.error,
        )

        if session is not None:
            response = _persist_turn(chats, session, output, response)
        return response

    @app.post("/api/sessions", status_code=201)
    def create_session(
        request: CreateSessionRequest,
        chats: Annotated[ChatStore, Depends(_chat_store)],
    ) -> SessionDetail:
        session = chats.create_session(
            title=request.title,
            board=request.board.model_dump(),
            max_questions=get_settings().max_questions_per_session,
        )
        return _session_detail(chats, session)

    @app.get("/api/sessions")
    def list_sessions(
        chats: Annotated[ChatStore, Depends(_chat_store)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[SessionSummary]:
        return [_session_summary(session) for session in chats.list_sessions(limit)]

    @app.get("/api/sessions/{session_id}")
    def get_session(
        session_id: str, chats: Annotated[ChatStore, Depends(_chat_store)]
    ) -> SessionDetail:
        session = chats.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id!r}")
        return _session_detail(chats, session)

    @app.patch("/api/sessions/{session_id}")
    def rename_session(
        session_id: str,
        request: RenameSessionRequest,
        chats: Annotated[ChatStore, Depends(_chat_store)],
    ) -> SessionDetail:
        session = chats.rename_session(session_id, request.title)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id!r}")
        return _session_detail(chats, session)

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(
        session_id: str, chats: Annotated[ChatStore, Depends(_chat_store)]
    ) -> None:
        if not chats.delete_session(session_id):
            raise HTTPException(status_code=404, detail=f"no session {session_id!r}")

    return app
