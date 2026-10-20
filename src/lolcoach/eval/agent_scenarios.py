"""Scenario definitions for the maintenance-agent evaluation suite.

A scenario is a named, hand-written case for the issue-fix or pr-review agent:
the issue (or pull request) it is handed, the repository state it starts from,
the scripted coder/reviewer output, and the *expectations* the run must meet.

Like the chat scenarios, the expectations are about observable behavior — which
states ran, what changed on disk, whether a PR opened and linked the issue, which
review event was submitted — never about model wording. The difference is that
these scenarios need no model at all: the coder and reviewer are scripted, so the
suite is hermetic and can run in CI on every change.

The suite lives in ``evals/agent_scenarios.yaml`` (checked in, versioned with the
code). Loading validates eagerly, so a typo in an id or a missing field fails
loudly before anything runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lolcoach.agent.fixer import FixPlan, Investigation
from lolcoach.agent.reviewer import ReviewCheck, ReviewComment, ReviewVerdict
from lolcoach.github.types import Issue, PullRequest
from lolcoach.values import as_bool, as_float, as_int, as_str, opt_dict, opt_list

logger = logging.getLogger(__name__)

FIX = "issue_fix"
REVIEW = "pr_review"
_KINDS = (FIX, REVIEW)


class AgentScenarioError(ValueError):
    """An agent scenario file is missing, unreadable, or malformed."""


@dataclass(frozen=True, slots=True)
class FixExpectations:
    """What a run of an issue-fix scenario must look like to count as a pass."""

    status: str = "succeeded"
    states: tuple[str, ...] = ()
    branch: str = ""
    files_changed: tuple[str, ...] | None = None
    no_writes: bool = False
    pr_opened: bool = True
    pr_links_issue: bool = True
    published: bool = True
    verification_passed: bool | None = None
    error_contains: str = ""


@dataclass(frozen=True, slots=True)
class ReviewExpectations:
    """What a run of a pr-review scenario must look like to count as a pass."""

    status: str = "succeeded"
    states: tuple[str, ...] = ()
    review_event: str = ""
    merged: bool = False
    error_contains: str = ""


@dataclass(frozen=True, slots=True)
class FixScenario:
    id: str
    name: str
    issue: Issue
    repo: dict[str, str]
    investigation: Investigation
    plan: FixPlan
    writes: tuple[tuple[str, str], ...]
    summary: str
    notes: tuple[str, ...]
    verify: dict[str, int]
    expect: FixExpectations = field(default_factory=FixExpectations)


@dataclass(frozen=True, slots=True)
class ReviewScenario:
    id: str
    name: str
    pull_request: PullRequest
    diff: str
    repo: dict[str, str]
    review: ReviewVerdict
    expect: ReviewExpectations = field(default_factory=ReviewExpectations)


AgentScenario = FixScenario | ReviewScenario


def default_agent_scenarios_path() -> Path:
    """The checked-in suite: ``<repo>/evals/agent_scenarios.yaml``."""
    return Path(__file__).resolve().parents[3] / "evals" / "agent_scenarios.yaml"


def kind_of(scenario: AgentScenario) -> str:
    return FIX if isinstance(scenario, FixScenario) else REVIEW


def load_agent_scenarios(path: Path | None = None) -> list[AgentScenario]:
    """Load and validate the suite. Raises :class:`AgentScenarioError` on any problem."""
    source = path or default_agent_scenarios_path()
    if not source.exists():
        raise AgentScenarioError(f"scenario file not found: {source}")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AgentScenarioError(f"invalid YAML in {source}: {exc}") from exc

    entries = opt_list(opt_dict(document).get("scenarios"))
    if not entries:
        raise AgentScenarioError(f"no scenarios defined in {source}")

    scenarios: list[AgentScenario] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        scenario = _parse_scenario(entry, index, source)
        if scenario.id in seen:
            raise AgentScenarioError(f"duplicate scenario id {scenario.id!r} in {source}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    logger.info(
        "Loaded maintenance-agent scenarios", extra={"data": {"count": len(scenarios)}}
    )
    return scenarios


def _parse_scenario(entry: object, index: int, source: Path) -> AgentScenario:
    raw = opt_dict(entry)
    scenario_id = as_str(raw.get("id")).strip()
    where = f"{source} scenario #{index + 1}"
    if not scenario_id:
        raise AgentScenarioError(f"{where}: missing 'id'")
    where = f"{source} ({scenario_id})"

    kind = as_str(raw.get("kind")).strip().lower()
    if kind not in _KINDS:
        raise AgentScenarioError(f"{where}: 'kind' must be one of {_KINDS}, got {kind!r}")
    name = as_str(raw.get("name")).strip() or scenario_id
    if kind == FIX:
        return _parse_fix(scenario_id, name, raw, where)
    return _parse_review(scenario_id, name, raw, where)


def _parse_fix(scenario_id: str, name: str, raw: dict[str, object], where: str) -> FixScenario:
    issue = _parse_issue(raw.get("issue"), where)
    investigation = _parse_investigation(raw.get("investigation"))
    implementation = opt_dict(raw.get("implementation"))
    verify = {command: as_int(code) for command, code in opt_dict(raw.get("verify")).items()}
    writes = tuple(
        (str(path), as_str(content))
        for path, content in opt_dict(implementation.get("writes")).items()
    )
    return FixScenario(
        id=scenario_id,
        name=name,
        issue=issue,
        repo=_string_map(raw.get("repo")),
        investigation=investigation,
        plan=_parse_plan(raw.get("plan")),
        writes=writes,
        summary=as_str(implementation.get("summary")),
        notes=_string_tuple(implementation.get("notes")),
        verify=verify,
        expect=_parse_fix_expectations(opt_dict(raw.get("expect")), where),
    )


def _parse_review(
    scenario_id: str, name: str, raw: dict[str, object], where: str
) -> ReviewScenario:
    pr = _parse_pull_request(raw.get("pull_request"), where)
    return ReviewScenario(
        id=scenario_id,
        name=name,
        pull_request=pr,
        diff=as_str(raw.get("diff")),
        repo=_string_map(raw.get("repo")),
        review=_parse_verdict(raw.get("review")),
        expect=_parse_review_expectations(opt_dict(raw.get("expect")), where),
    )


def _parse_issue(value: object, where: str) -> Issue:
    raw = opt_dict(value)
    number = as_int(raw.get("number"))
    if number <= 0:
        raise AgentScenarioError(f"{where}: issue needs a positive 'number'")
    if not as_str(raw.get("title")).strip():
        raise AgentScenarioError(f"{where}: issue needs a 'title'")
    return Issue(
        number=number,
        title=as_str(raw.get("title")).strip(),
        body=as_str(raw.get("body")),
        labels=_string_tuple(raw.get("labels")),
        state=as_str(raw.get("state"), "open"),
        url=as_str(raw.get("url")),
        author=as_str(raw.get("author")),
    )


def _parse_pull_request(value: object, where: str) -> PullRequest:
    raw = opt_dict(value)
    number = as_int(raw.get("number"))
    if number <= 0:
        raise AgentScenarioError(f"{where}: pull_request needs a positive 'number'")
    if not as_str(raw.get("title")).strip():
        raise AgentScenarioError(f"{where}: pull_request needs a 'title'")
    return PullRequest(
        number=number,
        title=as_str(raw.get("title")).strip(),
        body=as_str(raw.get("body")),
        head=as_str(raw.get("head")),
        base=as_str(raw.get("base"), "main"),
        state=as_str(raw.get("state"), "open"),
        url=as_str(raw.get("url")),
    )


def _parse_investigation(value: object) -> Investigation:
    raw = opt_dict(value)
    return Investigation(
        summary=as_str(raw.get("summary")),
        findings=_string_tuple(raw.get("findings")),
        files=_string_tuple(raw.get("files")),
        reproduces=as_bool(raw.get("reproduces")),
        confidence=as_float(raw.get("confidence")),
        is_real_issue=as_bool(raw.get("is_real_issue")),
    )


def _parse_plan(value: object) -> FixPlan:
    raw = opt_dict(value)
    return FixPlan(
        summary=as_str(raw.get("summary")),
        steps=_string_tuple(raw.get("steps")),
        files=_string_tuple(raw.get("files")),
        test_plan=as_str(raw.get("test_plan")),
    )


def _parse_verdict(value: object) -> ReviewVerdict:
    raw = opt_dict(value)
    return ReviewVerdict(
        decision=as_str(raw.get("decision"), "request_changes"),
        summary=as_str(raw.get("summary")),
        comments=tuple(
            ReviewComment(
                path=as_str(opt_dict(item).get("path")),
                line=as_int(opt_dict(item).get("line")),
                body=as_str(opt_dict(item).get("body")),
            )
            for item in opt_list(raw.get("comments"))
        ),
        checks=tuple(
            ReviewCheck(
                name=as_str(opt_dict(item).get("name")),
                ok=as_bool(opt_dict(item).get("ok"), True),
                detail=as_str(opt_dict(item).get("detail")),
            )
            for item in opt_list(raw.get("checks"))
        ),
    )


def _parse_fix_expectations(raw: dict[str, object], where: str) -> FixExpectations:
    status = as_str(raw.get("status"), "succeeded")
    if status not in ("succeeded", "failed"):
        raise AgentScenarioError(f"{where}: expect.status must be succeeded|failed")
    verification = raw.get("verification_passed")
    pr_opened = as_bool(raw.get("pr_opened"), True)
    return FixExpectations(
        status=status,
        states=_string_tuple(raw.get("states")),
        branch=as_str(raw.get("branch")),
        files_changed=(
            _string_tuple(raw.get("files_changed")) if "files_changed" in raw else None
        ),
        no_writes=as_bool(raw.get("no_writes")),
        pr_opened=pr_opened,
        pr_links_issue=as_bool(raw.get("pr_links_issue"), True),
        # Opening a PR means a branch was published; not opening one means none was.
        published=as_bool(raw.get("published"), pr_opened),
        verification_passed=None if verification is None else as_bool(verification),
        error_contains=as_str(raw.get("error_contains")),
    )


def _parse_review_expectations(raw: dict[str, object], where: str) -> ReviewExpectations:
    status = as_str(raw.get("status"), "succeeded")
    if status not in ("succeeded", "failed"):
        raise AgentScenarioError(f"{where}: expect.status must be succeeded|failed")
    event = as_str(raw.get("review_event")).strip().upper()
    if event and event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise AgentScenarioError(
            f"{where}: expect.review_event must be APPROVE|REQUEST_CHANGES|COMMENT"
        )
    return ReviewExpectations(
        status=status,
        states=_string_tuple(raw.get("states")),
        review_event=event,
        merged=as_bool(raw.get("merged")),
        error_contains=as_str(raw.get("error_contains")),
    )


def _string_map(value: object) -> dict[str, str]:
    return {str(key): as_str(item) for key, item in opt_dict(value).items()}


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(text for text in (as_str(item).strip() for item in opt_list(value)) if text)
