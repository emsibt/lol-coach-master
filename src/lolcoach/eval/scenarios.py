"""Golden scenarios for the agent evaluation suite.

A scenario is a named, human-written case: a message (and optionally a board or a
draft image) plus the *expectations* the run must meet. The expectations are about
observable behavior — tools called, board resolved, answer grounded, pick from the
engine — never about the model's exact wording, so a scenario states a requirement
rather than a transcript.

The suite lives in ``evals/scenarios.yaml`` (checked in, hand-authored, versioned
with the code). Loading validates the file eagerly so a typo in a scenario id or a
missing message fails loudly before any model is called.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lolcoach.values import as_str, opt_dict, opt_int, opt_list

logger = logging.getLogger(__name__)

_BOARD_KEYS = ("allies", "enemies", "ally_bans", "enemy_bans", "open_roles")
_RECOMMENDATION_MODES = ("required", "forbidden", "any")
_NARRATION_MODES = ("llm", "offline", "any")


class ScenarioError(ValueError):
    """A scenario file is missing, unreadable, or malformed."""


@dataclass(frozen=True, slots=True)
class Expectations:
    """What a run of the scenario must look like to count as a pass.

    Every field defaults to "do not check", so a scenario asserts only what it
    cares about. ``grounded`` and ``pick_from_engine`` default on because they are
    the product invariants every answer must satisfy.
    """

    status: str = "succeeded"
    tools_called: tuple[str, ...] = ()
    tools_not_called: tuple[str, ...] = ()
    max_tool_calls: int | None = None
    board: dict[str, tuple[str, ...]] = field(default_factory=dict)
    require_no_unresolved: bool = False
    recommendation: str = "any"
    mode: str = "any"
    grounded: bool = True
    pick_from_engine: bool = True
    reply_mentions: tuple[str, ...] = ()
    reply_excludes: tuple[str, ...] = ()
    error_contains: str = ""


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    name: str
    message: str
    board: dict[str, list[str]] = field(default_factory=dict)
    image: dict[str, str] | None = None
    expect: Expectations = field(default_factory=Expectations)


def default_scenarios_path() -> Path:
    """The checked-in suite: ``<repo>/evals/scenarios.yaml``."""
    return Path(__file__).resolve().parents[3] / "evals" / "scenarios.yaml"


def load_scenarios(path: Path | None = None) -> list[Scenario]:
    """Load and validate the suite. Raises :class:`ScenarioError` on any problem."""
    source = path or default_scenarios_path()
    if not source.exists():
        raise ScenarioError(f"scenario file not found: {source}")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"invalid YAML in {source}: {exc}") from exc

    raw = opt_dict(document).get("scenarios")
    entries = opt_list(raw)
    if not entries:
        raise ScenarioError(f"no scenarios defined in {source}")

    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        scenario = _parse_scenario(entry, index, source)
        if scenario.id in seen:
            raise ScenarioError(f"duplicate scenario id {scenario.id!r} in {source}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    logger.info("Loaded agent evaluation scenarios", extra={"data": {"count": len(scenarios)}})
    return scenarios


def _parse_scenario(entry: object, index: int, source: Path) -> Scenario:
    raw = opt_dict(entry)
    scenario_id = as_str(raw.get("id")).strip()
    message = as_str(raw.get("message")).strip()
    where = f"{source} scenario #{index + 1}"
    if not scenario_id:
        raise ScenarioError(f"{where}: missing 'id'")
    if not message:
        raise ScenarioError(f"{where} ({scenario_id}): missing 'message'")

    board = _parse_board(raw.get("board"), scenario_id)
    image = _parse_image(raw.get("image"), scenario_id)
    expect = _parse_expectations(opt_dict(raw.get("expect")), scenario_id)
    return Scenario(
        id=scenario_id,
        name=as_str(raw.get("name")).strip() or scenario_id,
        message=message,
        board=board,
        image=image,
        expect=expect,
    )


def _parse_board(value: object, scenario_id: str) -> dict[str, list[str]]:
    raw = opt_dict(value)
    board: dict[str, list[str]] = {}
    for key in _BOARD_KEYS:
        if key in raw:
            board[key] = _string_list(raw.get(key))
    return board


def _parse_image(value: object, scenario_id: str) -> dict[str, str] | None:
    if value is None:
        return None
    raw = opt_dict(value)
    data_b64 = as_str(raw.get("data_b64"))
    if not data_b64:
        raise ScenarioError(f"scenario {scenario_id!r}: image needs 'data_b64'")
    return {"media_type": as_str(raw.get("media_type"), "image/png"), "data_b64": data_b64}


def _parse_expectations(raw: dict[str, object], scenario_id: str) -> Expectations:
    recommendation = as_str(raw.get("recommendation"), "any")
    if recommendation not in _RECOMMENDATION_MODES:
        raise ScenarioError(
            f"scenario {scenario_id!r}: recommendation must be one of "
            f"{_RECOMMENDATION_MODES}, got {recommendation!r}"
        )
    mode = as_str(raw.get("mode"), "any")
    if mode not in _NARRATION_MODES:
        raise ScenarioError(
            f"scenario {scenario_id!r}: mode must be one of {_NARRATION_MODES}, got {mode!r}"
        )
    board: dict[str, tuple[str, ...]] = {}
    for key, value in opt_dict(raw.get("board")).items():
        board[str(key)] = tuple(_string_list(value))
    return Expectations(
        status=as_str(raw.get("status"), "succeeded"),
        tools_called=_string_tuple(raw.get("tools_called")),
        tools_not_called=_string_tuple(raw.get("tools_not_called")),
        max_tool_calls=opt_int(raw.get("max_tool_calls")),
        board=board,
        require_no_unresolved=_as_bool(raw.get("require_no_unresolved"), False),
        recommendation=recommendation,
        mode=mode,
        grounded=_as_bool(raw.get("grounded"), True),
        pick_from_engine=_as_bool(raw.get("pick_from_engine"), True),
        reply_mentions=_string_tuple(raw.get("reply_mentions")),
        reply_excludes=_string_tuple(raw.get("reply_excludes")),
        error_contains=as_str(raw.get("error_contains")).strip(),
    )


def _string_list(value: object) -> list[str]:
    return [text for text in (as_str(item).strip() for item in opt_list(value)) if text]


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(_string_list(value))


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default
