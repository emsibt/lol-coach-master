"""Tests for the evaluation runner: it drives the real agent loop and scores it.

A scripted ``LlmClient`` is injected through a factory (one fresh client per
scenario), so the suite is exercised end to end without a model or the network.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from lolcoach.agent.llm import LlmClient, LlmMessage, LlmReply, LlmToolCall
from lolcoach.config import get_settings
from lolcoach.eval.report import format_summary, to_dict, write_json
from lolcoach.eval.runner import EvalError, SuiteReport, run_suite
from lolcoach.eval.scenarios import Expectations, Scenario
from lolcoach.knowledge.ontology import get_ontology


class _ScriptedClient:
    """A scripted LlmClient: drives actions when tools are offered, else narrates."""

    provider = "scripted"

    def __init__(
        self, actions: list[tuple[str, dict[str, object]]], narration: str
    ) -> None:
        self._actions = list(actions)
        self._narration = narration

    def complete(
        self,
        _messages: Sequence[LlmMessage],
        tools: Sequence[dict[str, object]],
        *,
        tool_choice: str | None = None,
    ) -> LlmReply:
        if tools:
            if self._actions:
                name, args = self._actions.pop(0)
                return LlmReply("", (LlmToolCall(id=f"call-{name}", name=name, arguments=args),))
            return LlmReply("done", ())
        return LlmReply(self._narration, ())


_PICK_SCENARIO = Scenario(
    id="pick",
    name="pick",
    message="jinx vs their zeri, what do we pick?",
    expect=Expectations(
        tools_called=("set_board", "suggest_pick"),
        recommendation="required",
        mode="llm",
    ),
)


def _good_pick_client() -> _ScriptedClient:
    return _ScriptedClient(
        actions=[
            ("set_board", {"allies": ["jinx"], "enemies": ["zeri"]}),
            ("suggest_pick", {}),
        ],
        narration="Take the measured counter-pick.",
    )


_TIPS_SCENARIO = Scenario(
    id="tips",
    name="tips",
    message="how do I play against galio?",
    expect=Expectations(tools_called=("get_champion_tips",), mode="llm"),
)


def _bad_tips_client() -> _ScriptedClient:
    return _ScriptedClient(
        actions=[("get_champion_tips", {"champion": "galio"})],
        narration="Just pick Yasuo, trust me.",  # names a champion no tool sourced
    )


def _run(scenarios: list[Scenario], clients: Sequence[LlmClient], tmp_path: Path) -> SuiteReport:
    pool = iter(clients)
    return run_suite(
        scenarios,
        ontology=get_ontology(),
        client_factory=lambda: next(pool),
        store_path=tmp_path / "eval.db",
        settings=get_settings(),
    )


def test_a_passing_scenario_scores_clean(tmp_path: Path) -> None:
    report = _run([_PICK_SCENARIO], [_good_pick_client()], tmp_path)

    assert report.total == 1
    assert report.passed == 1
    assert report.ok
    result = report.results[0]
    assert result.tool_names == ("set_board", "suggest_pick")
    assert result.board["allies"] == ["jinx"]


def test_a_hallucinating_scenario_is_caught(tmp_path: Path) -> None:
    clients = [_good_pick_client(), _bad_tips_client()]
    report = _run([_PICK_SCENARIO, _TIPS_SCENARIO], clients, tmp_path)

    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1
    failed = report.results[1]
    assert failed.scenario_id == "tips"
    assert not failed.passed
    # The narrator's guard drops the unsourced reply for the grounded offline
    # summary, so the failure surfaces as mode=offline (not the llm answer).
    assert "mode" in {check.name for check in failed.failures}
    assert failed.mode == "offline"
    assert "yasuo" not in failed.reply.lower()


def test_missing_model_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(EvalError):
        run_suite(
            [_PICK_SCENARIO],
            ontology=get_ontology(),
            client_factory=lambda: None,
            store_path=tmp_path / "eval.db",
            settings=get_settings(),
        )


def test_report_serializes_and_formats(tmp_path: Path) -> None:
    clients = [_good_pick_client(), _bad_tips_client()]
    report = _run([_PICK_SCENARIO, _TIPS_SCENARIO], clients, tmp_path)

    summary = format_summary(report)
    assert "[PASS] pick" in summary
    assert "[FAIL] tips" in summary
    assert "1/2 scenarios passed" in summary

    payload = to_dict(report)
    assert payload["passed"] == 1 and payload["failed"] == 1
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    assert payload["provider"] == get_settings().llm_provider

    out = tmp_path / "report.json"
    write_json(report, out)
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["total"] == 2
