"""The chat agent's tool-loop, expressed as a workflow definition.

    ParseInput -> Think(Task) -> Route(Choice)
                     ^              | tool
                     |              v
                     +---------- RunTool(Task)      (loops back to Think)
                                    | final
                                    v
                    Route ---------> Finalize(Task) -> Succeeded
                                    | over budget
                                    v
                                 MaxSteps(Fail)

Running the loop as our own state machine is the point: the Trace Inspector
renders the agent's exact call journey (``Think`` chose ``set_board``, ``RunTool``
resolved it, ``Think`` chose ``suggest_pick``, ...). Task bodies close over the
ontology, the model client, and settings; only JSON-safe values travel in ``data``.

The LLM is required: if ``client`` is ``None`` the Think state raises and the
execution fails, and a failing model call fails the step too. There is no offline
planner, so a chat run never returns a fabricated answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from lolcoach.agent.agent import (
    AgentContext,
    ToolStep,
    choose_action,
    step_from_dict,
    step_to_dict,
)
from lolcoach.agent.llm import ImagePart, LlmClient
from lolcoach.agent.narrator import narrate_final
from lolcoach.agent.tools import Board, ToolBox, normalize_board
from lolcoach.config import Settings, get_settings
from lolcoach.knowledge.ontology import Ontology
from lolcoach.values import as_int, as_str, opt_dict, opt_list
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
class AgentChat:
    workflow: Workflow
    tasks: dict[str, TaskFn]
    predicates: dict[str, PredicateFn]


def _steps(value: object) -> list[ToolStep]:
    return [step_from_dict(item) for item in opt_list(value)]


def _image(value: object) -> ImagePart | None:
    raw = opt_dict(value)
    data_b64 = as_str(raw.get("data_b64"))
    if not data_b64:
        return None
    return ImagePart(media_type=as_str(raw.get("media_type"), "image/png"), data_b64=data_b64)


def _turns(value: object) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for item in opt_list(value):
        raw = opt_dict(item)
        role = as_str(raw.get("role"))
        text = as_str(raw.get("text"))
        if role and text:
            out.append((role, text))
    return tuple(out)


def _rec_from_steps(steps: list[ToolStep]) -> dict[str, object] | None:
    for step in reversed(steps):
        if step.name == "suggest_pick" and step.ok:
            return {
                "counter": step.payload.get("counter"),
                "synergy": step.payload.get("synergy"),
                "traces": step.payload.get("traces"),
                "problems": step.payload.get("problems"),
            }
    return None


def build_agent_chat(
    ontology: Ontology,
    *,
    client: LlmClient | None = None,
    settings: Settings | None = None,
) -> AgentChat:
    """Build the agent-chat workflow plus its task/predicate registries."""
    active = settings or get_settings()
    toolbox = ToolBox(ontology)

    def parse_input(data: dict[str, object]) -> dict[str, object]:
        question = as_str(data.get("question"))
        board = normalize_board(data.get("board"))
        unresolved: list[object] = []
        if any(board.get(key) for key in ("allies", "enemies", "ally_bans", "enemy_bans")):
            resolved = toolbox.set_board(board)
            board = normalize_board(resolved.payload.get("board"))
            unresolved = list(opt_list(resolved.payload.get("unresolved")))
        return {
            "question": question,
            "board": board,
            "image": opt_dict(data.get("image")) or None,
            "history": [step_to_dict(step) for step in _steps(data.get("history"))],
            "turns": [list(item) for item in _turns(data.get("turns"))],
            "unresolved": unresolved,
            "max_tool_calls": as_int(data.get("max_tool_calls"), active.max_tool_calls),
        }

    def think(data: dict[str, object]) -> dict[str, object]:
        context = AgentContext(
            question=as_str(data.get("question")),
            board=normalize_board(data.get("board")),
            image=_image(data.get("image")),
            history=tuple(_steps(data.get("history"))),
            turns=_turns(data.get("turns")),
        )
        action = choose_action(context, client=client, settings=active)
        return {"action": action}

    def run_tool(data: dict[str, object]) -> dict[str, object]:
        action = opt_dict(data.get("action"))
        name = as_str(action.get("name"))
        arguments = opt_dict(action.get("arguments"))
        call_id = as_str(action.get("call_id")) or uuid4().hex
        board = normalize_board(data.get("board"))
        try:
            result = toolbox.call(name, arguments, board)
            ok, payload = result.ok, result.payload
        except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the loop
            logger.exception("Agent tool failed", extra={"data": {"tool": name}})
            ok, payload = False, {"error": f"{type(exc).__name__}: {exc}"}

        step = ToolStep(
            call_id=call_id, name=name, arguments=arguments, ok=ok, payload=payload
        )
        history = [step_to_dict(existing) for existing in _steps(data.get("history"))]
        history.append(step_to_dict(step))
        applies_board = name == "set_board" and ok
        new_board: Board = normalize_board(payload.get("board")) if applies_board else board
        return {"history": history, "board": new_board, "last_step": step_to_dict(step)}

    def finalize(data: dict[str, object]) -> dict[str, object]:
        steps = _steps(data.get("history"))
        board = normalize_board(data.get("board"))
        narration = narrate_final(
            as_str(data.get("question")),
            steps,
            board,
            client=client,
            settings=active,
            ontology=ontology,
        )
        unresolved: list[object] = list(opt_list(data.get("unresolved")))
        for step in steps:
            if step.name == "set_board":
                unresolved.extend(opt_list(step.payload.get("unresolved")))
        return {
            "reply": narration.text,
            "mode": narration.mode,
            "tool_calls": [
                {"name": step.name, "ok": step.ok, "arguments": step.arguments} for step in steps
            ],
            "recommendation": _rec_from_steps(steps),
            "unresolved": unresolved,
        }

    def action_is_final(data: dict[str, object]) -> bool:
        return as_str(opt_dict(data.get("action")).get("kind")) == "final"

    def tool_budget_exhausted(data: dict[str, object]) -> bool:
        budget = as_int(data.get("max_tool_calls"), active.max_tool_calls)
        return len(opt_list(data.get("history"))) >= budget

    states: dict[str, State] = {
        "ParseInput": TaskState("ParseInput", "parse_input", next="Think"),
        "Think": TaskState("Think", "think", next="Route"),
        "Route": ChoiceState(
            "Route",
            (
                ChoiceRule("action_is_final", "Finalize"),
                ChoiceRule("tool_budget_exhausted", "MaxSteps"),
            ),
            default="RunTool",
        ),
        "RunTool": TaskState("RunTool", "run_tool", next="Think"),
        "Finalize": TaskState("Finalize", "finalize", next="Succeeded"),
        "Succeeded": SucceedState("Succeeded"),
        "MaxSteps": FailState("MaxSteps", error="agent exceeded its tool-call budget"),
    }

    workflow = Workflow(name="agent-chat", version="1", start_at="ParseInput", states=states)
    tasks: dict[str, TaskFn] = {
        "parse_input": parse_input,
        "think": think,
        "run_tool": run_tool,
        "finalize": finalize,
    }
    predicates: dict[str, PredicateFn] = {
        "action_is_final": action_is_final,
        "tool_budget_exhausted": tool_budget_exhausted,
    }
    return AgentChat(workflow=workflow, tasks=tasks, predicates=predicates)
