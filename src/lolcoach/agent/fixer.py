"""The fixer agent: investigate an issue, plan a fix, implement it.

Three phases, each a bounded tool-loop over the workspace, each ending in a
structured result the workflow persists and the Trace Inspector renders:

    investigate -> Investigation  (is this a real issue? what evidence?)
    plan        -> FixPlan        (the steps, and the files they touch)
    implement   -> Implementation (what actually changed on disk)

The *files changed* in :class:`Implementation` come from the workspace
(``git status``), never from the model's own claim — the same "the engine
decides, the model narrates" rule the chat agent follows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from lolcoach.agent.coding import (
    LoopBudget,
    describe_issue,
    fn,
    run_coding_loop,
    workspace_tools,
)
from lolcoach.agent.llm import LlmClient
from lolcoach.agent.workspace import Workspace
from lolcoach.config import Settings
from lolcoach.github.types import Issue
from lolcoach.values import as_bool, as_float, as_str, opt_dict, opt_list

_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_BOOL = {"type": "boolean"}
_STR_ARRAY = {"type": "array", "items": _STRING}


def _strings(value: object) -> tuple[str, ...]:
    return tuple(text for text in (as_str(item).strip() for item in opt_list(value)) if text)


@dataclass(frozen=True, slots=True)
class Investigation:
    summary: str = ""
    findings: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    reproduces: bool = False
    confidence: float = 0.0
    is_real_issue: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "findings": list(self.findings),
            "files": list(self.files),
            "reproduces": self.reproduces,
            "confidence": self.confidence,
            "is_real_issue": self.is_real_issue,
        }

    @classmethod
    def from_dict(cls, value: object) -> Investigation:
        raw = opt_dict(value)
        return cls(
            summary=as_str(raw.get("summary")),
            findings=_strings(raw.get("findings")),
            files=_strings(raw.get("files")),
            reproduces=as_bool(raw.get("reproduces")),
            confidence=as_float(raw.get("confidence")),
            is_real_issue=as_bool(raw.get("is_real_issue")),
        )


@dataclass(frozen=True, slots=True)
class FixPlan:
    summary: str = ""
    steps: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    test_plan: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "steps": list(self.steps),
            "files": list(self.files),
            "test_plan": self.test_plan,
        }

    @classmethod
    def from_dict(cls, value: object) -> FixPlan:
        raw = opt_dict(value)
        return cls(
            summary=as_str(raw.get("summary")),
            steps=_strings(raw.get("steps")),
            files=_strings(raw.get("files")),
            test_plan=as_str(raw.get("test_plan")),
        )


@dataclass(frozen=True, slots=True)
class Implementation:
    summary: str = ""
    files_changed: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "files_changed": list(self.files_changed),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, value: object) -> Implementation:
        raw = opt_dict(value)
        return cls(
            summary=as_str(raw.get("summary")),
            files_changed=_strings(raw.get("files_changed")),
            notes=_strings(raw.get("notes")),
        )


class Coder(Protocol):
    """The fixer's brain. The workflow depends on this, not on a transport."""

    def investigate(self, issue: Issue, workspace: Workspace) -> Investigation: ...
    def plan(self, issue: Issue, investigation: Investigation, workspace: Workspace) -> FixPlan: ...
    def implement(self, issue: Issue, plan: FixPlan, workspace: Workspace) -> Implementation: ...


_INVESTIGATE_SYSTEM = """\
You are the investigation phase of an autonomous maintenance agent working in a
checked-out repository. You do NOT edit anything in this phase.

Read the issue, then use the read tools (`list_files`, `read_file`, `run_command`)
to find the code that the issue is about and to decide, from the repository's own
evidence, whether the issue describes a real, actionable defect.

Rules:
- Base every finding on something you actually read or ran. Quote the file path
  (and line where you can) that supports each finding.
- `is_real_issue` is true only when you can point to code that is genuinely
  wrong, missing, or inconsistent with what the issue claims. A vague feature
  request, a question, or a report you cannot substantiate is NOT a real issue.
- `confidence` rates the code evidence, not whether you reproduced the defect:
  a specific defect you can point to in the code is 0.7-1.0 even when you could
  not run a reproduction. Reserve values below 0.5 for a finding you cannot tie
  to concrete code. The workflow proceeds only at 0.5 or higher, so do not
  downrate a substantiated defect just because the environment cannot reproduce it.
- `reproduces` is true only if you ran something that demonstrated the defect.
- Submit as soon as the evidence is sufficient: read the files the issue names and
  their immediate call sites, then stop. Do not survey the whole repository.
- When done, call `submit_investigation`. Do not write prose outside the tool call.
"""

_PLAN_SYSTEM = """\
You are the planning phase of an autonomous maintenance agent. Given an issue and
the investigation's findings, produce a concrete, minimal fix plan.

Rules:
- Prefer the smallest change that resolves the issue. Do not refactor unrelated
  code and do not add dependencies.
- List the files you expect to change, the ordered steps, and how the change will
  be verified (which tests or commands prove it).
- When done, call `submit_plan`.
"""

_IMPLEMENT_SYSTEM = """\
You are the implementation phase of an autonomous maintenance agent working in a
checked-out repository. Carry out the approved plan.

Rules:
- Make the change with `write_file` (full file contents), then run the tests/lint
  with `run_command` to confirm it works. Iterate until the verification passes.
- Change only what the plan requires. Never touch `.env` or any secret file; the
  workspace will refuse it.
- When the change is complete and verified, call `submit_implementation` with a
  short summary and any notes. Do not write prose outside the tool call.
"""


def _terminal(
    name: str, description: str, properties: dict[str, object], required: Sequence[str]
) -> dict[str, object]:
    return fn(name, description, properties, required)


class LlmCoder:
    """A :class:`Coder` backed by a chat model running a workspace tool-loop."""

    def __init__(
        self,
        client: LlmClient | None,
        settings: Settings,
        *,
        budget: LoopBudget | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._budget = budget or LoopBudget.from_settings(settings)

    def investigate(self, issue: Issue, workspace: Workspace) -> Investigation:
        tools = [
            *workspace_tools(writable=False),
            _terminal(
                "submit_investigation",
                "Submit your investigation verdict. Call this to finish the phase.",
                {
                    "summary": _STRING,
                    "findings": _STR_ARRAY,
                    "files": _STR_ARRAY,
                    "reproduces": _BOOL,
                    "confidence": _NUMBER,
                    "is_real_issue": _BOOL,
                },
                ["summary", "findings", "reproduces", "confidence", "is_real_issue"],
            ),
        ]
        args = run_coding_loop(
            self._client,
            self._settings,
            system=_INVESTIGATE_SYSTEM,
            user=describe_issue(issue.title, issue.body, f"Repository: {workspace!r}"),
            tools=tools,
            terminal="submit_investigation",
            workspace=workspace,
            max_rounds=self._budget.investigate,
        )
        return Investigation.from_dict(args)

    def plan(self, issue: Issue, investigation: Investigation, workspace: Workspace) -> FixPlan:
        tools = [
            *workspace_tools(writable=False),
            _terminal(
                "submit_plan",
                "Submit the fix plan. Call this to finish the phase.",
                {
                    "summary": _STRING,
                    "steps": _STR_ARRAY,
                    "files": _STR_ARRAY,
                    "test_plan": _STRING,
                },
                ["summary", "steps", "files", "test_plan"],
            ),
        ]
        extra = (
            "Investigation findings:\n"
            + "\n".join(f"- {finding}" for finding in investigation.findings)
            + f"\nSummary: {investigation.summary}"
        )
        args = run_coding_loop(
            self._client,
            self._settings,
            system=_PLAN_SYSTEM,
            user=describe_issue(issue.title, issue.body, extra),
            tools=tools,
            terminal="submit_plan",
            workspace=workspace,
            max_rounds=self._budget.plan,
        )
        return FixPlan.from_dict(args)

    def implement(self, issue: Issue, plan: FixPlan, workspace: Workspace) -> Implementation:
        tools = [
            *workspace_tools(writable=True),
            _terminal(
                "submit_implementation",
                "Submit the completed change. Call this to finish the phase.",
                {"summary": _STRING, "notes": _STR_ARRAY},
                ["summary"],
            ),
        ]
        steps = "\n".join(f"{index + 1}. {step}" for index, step in enumerate(plan.steps))
        extra = "Approved plan:\n" + steps
        args = run_coding_loop(
            self._client,
            self._settings,
            system=_IMPLEMENT_SYSTEM,
            user=describe_issue(issue.title, issue.body, extra),
            tools=tools,
            terminal="submit_implementation",
            workspace=workspace,
            max_rounds=self._budget.implement,
        )
        # Files changed come from the workspace, not the model's claim.
        return Implementation(
            summary=as_str(args.get("summary")),
            files_changed=tuple(workspace.changed_files()),
            notes=_strings(args.get("notes")),
        )


@dataclass(frozen=True, slots=True)
class ScriptedCoder:
    """A :class:`Coder` that returns fixed results — for tests and the eval suite.

    It still writes ``writes`` through the workspace so ``changed_files`` is real,
    which lets an eval check assert on the actual file effects of a run.
    """

    investigation: Investigation = Investigation()
    plan_result: FixPlan = FixPlan()
    implementation_summary: str = ""
    writes: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()

    def investigate(self, issue: Issue, workspace: Workspace) -> Investigation:
        return self.investigation

    def plan(self, issue: Issue, investigation: Investigation, workspace: Workspace) -> FixPlan:
        return self.plan_result

    def implement(self, issue: Issue, plan: FixPlan, workspace: Workspace) -> Implementation:
        for path, content in self.writes:
            workspace.write(path, content)
        return Implementation(
            summary=self.implementation_summary,
            files_changed=tuple(workspace.changed_files()),
            notes=self.notes,
        )
