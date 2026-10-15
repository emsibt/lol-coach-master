"""Tests for the evaluation checks, exercised against synthetic runs."""

from __future__ import annotations

from lolcoach.eval.checks import CheckOutcome, evaluate
from lolcoach.eval.scenarios import Expectations, Scenario
from lolcoach.knowledge.ontology import get_ontology
from lolcoach.workflow.executor import ExecutionResult


def _result(
    output: dict[str, object], *, status: str = "succeeded", error: str | None = None
) -> ExecutionResult:
    return ExecutionResult(execution_id="e1", status=status, output=output, error=error, events=())


def _step(
    name: str, payload: dict[str, object], sources: tuple[str, ...] = ()
) -> dict[str, object]:
    return {
        "call_id": f"c-{name}",
        "name": name,
        "arguments": {},
        "ok": True,
        "payload": payload,
        "sources": list(sources),
    }


def _outcomes(scenario: Scenario, result: ExecutionResult) -> dict[str, CheckOutcome]:
    return {outcome.name: outcome for outcome in evaluate(scenario, result, get_ontology())}


def test_tools_called_requires_order() -> None:
    scenario = Scenario(
        id="a",
        name="a",
        message="m",
        expect=Expectations(tools_called=("set_board", "suggest_pick")),
    )
    ordered = _result({"tool_calls": [{"name": "set_board"}, {"name": "suggest_pick"}]})
    reversed_ = _result({"tool_calls": [{"name": "suggest_pick"}, {"name": "set_board"}]})

    assert _outcomes(scenario, ordered)["tools_called"].ok
    assert not _outcomes(scenario, reversed_)["tools_called"].ok


def test_forbidden_tool_is_flagged() -> None:
    scenario = Scenario(
        id="a", name="a", message="m", expect=Expectations(tools_not_called=("suggest_pick",))
    )
    result = _result({"tool_calls": [{"name": "suggest_pick"}]})
    assert not _outcomes(scenario, result)["tools_not_called"].ok


def test_board_expectation_reports_missing_slugs() -> None:
    scenario = Scenario(
        id="a",
        name="a",
        message="m",
        expect=Expectations(board={"allies": ("jinx",), "enemies": ("zeri",)}),
    )
    result = _result({"board": {"allies": ["jinx"], "enemies": ["lux"]}})
    outcome = _outcomes(scenario, result)["board"]
    assert not outcome.ok
    assert "zeri" in outcome.detail


def test_budget_bounds_the_tool_call_count() -> None:
    scenario = Scenario(
        id="a", name="a", message="m", expect=Expectations(max_tool_calls=1)
    )
    result = _result({"tool_calls": [{"name": "set_board"}, {"name": "suggest_pick"}]})
    assert not _outcomes(scenario, result)["max_tool_calls"].ok


def test_grounding_flags_an_invented_champion() -> None:
    scenario = Scenario(id="a", name="a", message="m")
    payload: dict[str, object] = {
        "board": {"allies": ["jinx"], "enemies": ["zeri"]},
        "unresolved": [],
    }
    board: dict[str, object] = {"allies": ["jinx"], "enemies": ["zeri"]}
    grounded = _result(
        {
            "history": [_step("set_board", payload)],
            "board": board,
            "reply": "Take Jinx into Zeri.",
        }
    )
    invented = _result(
        {
            "history": [_step("set_board", payload)],
            "board": board,
            "reply": "Definitely pick Yasuo.",
        }
    )
    assert _outcomes(scenario, grounded)["grounded"].ok
    assert not _outcomes(scenario, invented)["grounded"].ok


def test_mode_expectation_is_enforced() -> None:
    scenario = Scenario(id="a", name="a", message="m", expect=Expectations(mode="llm"))
    assert _outcomes(scenario, _result({"mode": "llm"}))["mode"].ok
    assert not _outcomes(scenario, _result({"mode": "offline"}))["mode"].ok


def test_forbidden_recommendation_flags_a_pick() -> None:
    scenario = Scenario(
        id="a", name="a", message="m", expect=Expectations(recommendation="forbidden")
    )
    with_pick = _result({"recommendation": {"counter": {"slug": "lux"}}})
    assert not _outcomes(scenario, with_pick)["recommendation"].ok
    assert _outcomes(scenario, _result({}))["recommendation"].ok


def test_pick_from_engine_is_not_applicable_without_a_pick() -> None:
    scenario = Scenario(id="a", name="a", message="m")
    outcome = _outcomes(scenario, _result({}))["pick_from_engine"]
    assert outcome.ok
    assert "not applicable" in outcome.detail


def test_pick_from_engine_flags_a_pick_the_engine_did_not_make() -> None:
    # An empty board has no pick to score, so any narrated pick is unsourced.
    scenario = Scenario(id="a", name="a", message="m")
    result = _result({"recommendation": {"counter": {"slug": "lux", "name": "Lux"}}})
    assert not _outcomes(scenario, result)["pick_from_engine"].ok


def test_pick_from_engine_matches_a_real_engine_run() -> None:
    from lolcoach.engine.draft_state import DraftState
    from lolcoach.engine.explain import recommendation_to_dict
    from lolcoach.engine.scoring import evaluate_draft

    ontology = get_ontology()
    state = DraftState(allies=("jinx",), enemies=("zeri",))
    fresh = recommendation_to_dict(evaluate_draft(ontology, state))
    scenario = Scenario(id="a", name="a", message="m")
    result = _result(
        {
            "board": {"allies": ["jinx"], "enemies": ["zeri"]},
            "recommendation": fresh,
        }
    )
    assert _outcomes(scenario, result)["pick_from_engine"].ok
