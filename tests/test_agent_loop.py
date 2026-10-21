"""Tests for the agent chat loop (the tools wired into our workflow state machine).

A scripted ``LlmClient`` drives the loop, so the recorded events are the agent's
auditable call journey. The loop has no offline planner: a missing client or a
failing model call fails the Think step.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

from lolcoach.agent.llm import LlmClient, LlmMessage, LlmReply, LlmToolCall
from lolcoach.agent.narrator import unsourced_champions
from lolcoach.agent.tools import LiveDataError, LiveDataProvider
from lolcoach.knowledge.ontology import get_ontology
from lolcoach.knowledge.reference import (
    ChampionBuild,
    ChampionReference,
    ItemSet,
    ReferenceData,
    RunePage,
)
from lolcoach.workflow.executor import ExecutionResult, WorkflowExecutor
from lolcoach.workflow.store import ExecutionStore
from lolcoach.workflow.workflows.agent_chat import build_agent_chat


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast("list[object]", value)


class _FakeClient:
    """A scripted LlmClient: drives actions when tools are offered, else narrates."""

    provider = "fake"

    def __init__(
        self,
        actions: list[tuple[str | None, dict[str, object]]],
        narration: str,
    ) -> None:
        self._actions = list(actions)
        self._narration = narration
        self.offered: list[set[str]] = []

    def complete(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[dict[str, object]],
        *,
        tool_choice: str | None = None,
    ) -> LlmReply:
        self.offered.append({str(_dict(_dict(spec)["function"])["name"]) for spec in tools})
        if tools:
            name, args = self._actions.pop(0) if self._actions else (None, {})
            if name is None:
                return LlmReply("done", ())
            return LlmReply("", (LlmToolCall(id=f"call-{name}", name=name, arguments=args),))
        return LlmReply(self._narration, ())


_REFERENCE = ReferenceData(
    champions={
        "galio": ChampionReference(
            slug="galio",
            name="Galio",
            lore="lore",
            blurb="blurb",
            tags=("Tank",),
            ally_tips=("Play safe",),
            enemy_tips=("Punish cooldowns",),
            abilities=(),
        )
    },
    builds={
        ("galio", "mid"): ChampionBuild(
            slug="galio",
            position="mid",
            skill_order=("Q", "E"),
            item_sets=(ItemSet("core", ("Luden's",), (6655,), 0.54, 0.3),),
            runes=(RunePage("Sorcery", ("Summon Aery",)),),
        )
    },
    items={},
)


def _run(
    tmp_path: Path,
    message: str,
    *,
    board: dict[str, list[str]] | None = None,
    image: dict[str, str] | None = None,
    max_tool_calls: int | None = None,
    client: LlmClient | None = None,
    reference: ReferenceData | None = None,
    live: LiveDataProvider | None = None,
) -> ExecutionResult:
    store = ExecutionStore.connect(tmp_path / "agent.db")
    try:
        built = build_agent_chat(
            get_ontology(), client=client, reference=reference, live=live
        )
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        initial: dict[str, object] = {"question": message, "board": board or {}}
        if image is not None:
            initial["image"] = image
        if max_tool_calls is not None:
            initial["max_tool_calls"] = max_tool_calls
        return executor.run(initial)
    finally:
        store.close()


def _planner() -> _FakeClient:
    return _FakeClient(
        actions=[
            ("set_board", {"allies": ["jinx"], "enemies": ["zeri"]}),
            ("suggest_pick", {}),
        ],
        narration="Take the counter-pick; the tools measured it.",
    )


class _BoomClient:
    """A client whose call always fails, to prove the Think step fails loudly."""

    provider = "boom"

    def complete(
        self,
        _messages: Sequence[LlmMessage],
        _tools: Sequence[dict[str, object]],
        *,
        tool_choice: str | None = None,
    ) -> LlmReply:
        raise RuntimeError("upstream 500")


def test_loop_parses_a_fuzzy_message_and_advises(tmp_path: Path) -> None:
    result = _run(tmp_path, "jinx vs their zeri", client=_planner())

    assert result.status == "succeeded"
    board = _dict(result.output["board"])
    assert board["allies"] == ["jinx"]
    assert board["enemies"] == ["zeri"]
    assert result.output["mode"] == "llm"
    assert str(result.output["reply"]).strip()

    recommendation = _dict(result.output["recommendation"])
    assert recommendation["counter"] or recommendation["synergy"]

    history = _list(result.output["history"])
    names = [_dict(step)["name"] for step in history]
    assert names[:2] == ["set_board", "suggest_pick"]


def test_loop_events_are_gapless_and_show_the_call_journey(tmp_path: Path) -> None:
    result = _run(tmp_path, "jinx vs their zeri", client=_planner())

    assert [event.seq for event in result.events] == list(range(1, len(result.events) + 1))
    assert result.events[0].type == "EXECUTION_STARTED"
    assert result.events[-1].type == "EXECUTION_SUCCEEDED"

    entered = [event.state_name for event in result.events if event.type == "STATE_ENTERED"]
    assert entered[0] == "ParseInput"
    assert "Think" in entered
    assert entered.count("RunTool") == 2
    assert entered[-1] == "Finalize"


def test_agent_respects_the_tool_budget(tmp_path: Path) -> None:
    result = _run(tmp_path, "jinx vs their zeri", max_tool_calls=1, client=_planner())

    assert result.status == "failed"
    assert result.error == "agent exceeded its tool-call budget"
    assert any(event.state_name == "MaxSteps" for event in result.events)


def test_missing_client_fails_the_think_step(tmp_path: Path) -> None:
    result = _run(tmp_path, "jinx vs their zeri")  # no client configured

    assert result.status == "failed"
    assert result.error is not None and "LlmUnavailableError" in result.error
    assert any(
        event.type == "STATE_FAILED" and event.state_name == "Think" for event in result.events
    )
    assert result.events[-1].type == "EXECUTION_FAILED"
    assert result.output.get("reply") is None


def test_model_error_fails_the_think_step(tmp_path: Path) -> None:
    result = _run(tmp_path, "jinx vs their zeri", client=_BoomClient())

    assert result.status == "failed"
    assert result.error is not None and "LLM call failed" in result.error
    assert result.output.get("reply") is None


def test_narration_naming_an_unsourced_champion_is_discarded(tmp_path: Path) -> None:
    client = _FakeClient(
        actions=[("set_board", {"allies": ["jinx"], "enemies": ["zeri"]})],
        narration="Pick Yasuo - he wins every lane.",
    )
    result = _run(tmp_path, "who should we pick?", client=client)

    assert result.status == "succeeded"
    assert result.output["mode"] == "offline"
    assert "yasuo" not in str(result.output["reply"]).lower()


def test_unsourced_champions_flags_invented_names() -> None:
    ontology = get_ontology()
    assert unsourced_champions("Pick Yasuo.", {"jinx"}, ontology) == {"yasuo"}
    assert unsourced_champions("Jinx with Zeri.", {"jinx", "zeri"}, ontology) == set()


def test_reference_tools_are_only_offered_when_loaded(tmp_path: Path) -> None:
    without = _FakeClient(actions=[], narration="ok")
    _run(tmp_path, "hi", client=without)
    assert "get_champion_build" not in without.offered[0]

    with_ref = _FakeClient(actions=[], narration="ok")
    _run(tmp_path, "hi", client=with_ref, reference=_REFERENCE)
    assert {"get_champion_tips", "get_champion_build"} <= with_ref.offered[0]


def test_reference_call_records_its_sources_on_the_step(tmp_path: Path) -> None:
    """``sources`` must survive the workflow's dict round trip, and stay out of
    the payload the model sees."""
    client = _FakeClient(
        actions=[("get_champion_build", {"champion": "galio"})],
        narration="Start with Luden's and max Q first.",
    )
    result = _run(tmp_path, "what do I build?", client=client, reference=_REFERENCE)

    assert result.status == "succeeded"
    assert result.output["mode"] == "llm"
    build_step = _dict(_list(result.output["history"])[0])
    assert build_step["name"] == "get_champion_build"
    assert build_step["sources"] == ["galio"]
    assert "sources" not in _dict(build_step["payload"])


def test_reference_prose_naming_another_champion_is_discarded(tmp_path: Path) -> None:
    """The guard change, end to end: a tip mentions Yasuo, a reply that repeats
    it is dropped for the grounded offline summary."""
    client = _FakeClient(
        actions=[("get_champion_tips", {"champion": "galio"})],
        narration="Galio beats Yasuo, so take him.",
    )
    result = _run(tmp_path, "how do I play galio?", client=client, reference=_REFERENCE)

    assert result.status == "succeeded"
    assert result.output["mode"] == "offline"
    assert str(result.output["reply"]).startswith("Galio:")


def test_a_failing_tool_does_not_kill_the_run(tmp_path: Path) -> None:
    """A broker whose reference data raises: the step fails, the loop continues."""

    class _BoomLookup:
        def champion(self, slug: str) -> object:
            raise RuntimeError("reference store offline")

    client = _FakeClient(actions=[("get_champion_tips", {"champion": "galio"})], narration="ok")
    broken = ReferenceData(champions=cast("dict[str, ChampionReference]", {"galio": _BoomLookup()}))
    result = _run(tmp_path, "how do I play galio?", client=client, reference=broken)

    assert result.status == "succeeded"
    failed = [step for step in _list(result.output["history"]) if not _dict(step)["ok"]]
    assert [step["name"] for step in failed] == ["get_champion_tips"]


class _FakeLive:
    """A LiveDataProvider stand-in; ``failing`` makes every lookup raise."""

    def __init__(self, *, failing: bool = False) -> None:
        self._failing = failing

    def summoner_profile(self, riot_id: str, region: str = "") -> dict[str, object]:
        if self._failing:
            raise LiveDataError("upstream 503")
        return {"riot_id": riot_id, "region": region or "NA", "queues": [], "season": {}}

    def lane_matchup(self, champion: str, opponent: str, role: str) -> dict[str, object]:
        if self._failing:
            raise LiveDataError("upstream 503")
        return {
            "champion": champion,
            "opponent": opponent,
            "position": role,
            "tip": "a measured tip",
        }


def test_live_tools_are_only_offered_when_injected(tmp_path: Path) -> None:
    without = _FakeClient(actions=[], narration="ok")
    _run(tmp_path, "hi", client=without)
    assert "get_summoner_profile" not in without.offered[0]

    with_live = _FakeClient(actions=[], narration="ok")
    _run(tmp_path, "hi", client=with_live, live=_FakeLive())
    assert {"get_summoner_profile", "get_lane_matchup_guide"} <= with_live.offered[0]


def test_a_failing_live_lookup_does_not_kill_the_run(tmp_path: Path) -> None:
    client = _FakeClient(
        actions=[("get_summoner_profile", {"riot_id": "Name#TAG"})], narration="ok"
    )
    result = _run(tmp_path, "what's my rank?", client=client, live=_FakeLive(failing=True))

    assert result.status == "succeeded"
    step = _dict(_list(result.output["history"])[0])
    assert step["ok"] is False
    assert "503" in str(step["payload"])


def test_the_lane_matchup_guide_records_both_sources(tmp_path: Path) -> None:
    action = ("get_lane_matchup_guide", {"champion": "galio", "opponent": "ahri", "role": "mid"})
    client = _FakeClient(
        actions=[action], narration="Trade with Galio when Ahri steps up."
    )
    result = _run(tmp_path, "galio vs ahri mid?", client=client, live=_FakeLive())

    assert result.status == "succeeded"
    assert result.output["mode"] == "llm"
    step = _dict(_list(result.output["history"])[0])
    assert step["sources"] == ["galio", "ahri"]
