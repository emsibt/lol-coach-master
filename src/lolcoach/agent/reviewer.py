"""The reviewer agent: inspect a pull request's diff and return a verdict.

Read-only by construction: the tool set has no ``write_file``, so the reviewer
physically cannot edit the change it is judging. The verdict is structured
(``approve`` or ``request_changes`` plus per-line comments and named checks), so
the workflow can gate on it deterministically and the Trace Inspector can render
exactly what was reviewed and why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lolcoach.agent.coding import LoopBudget, fn, run_coding_loop, workspace_tools
from lolcoach.agent.llm import LlmClient
from lolcoach.agent.workspace import Workspace
from lolcoach.config import Settings
from lolcoach.github.types import PullRequest
from lolcoach.values import as_bool, as_int, as_str, opt_dict, opt_list

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_BOOL = {"type": "boolean"}
_STR_ARRAY = {"type": "array", "items": _STRING}

APPROVE = "approve"
REQUEST_CHANGES = "request_changes"


def _strings(value: object) -> tuple[str, ...]:
    return tuple(text for text in (as_str(item).strip() for item in opt_list(value)) if text)


@dataclass(frozen=True, slots=True)
class ReviewComment:
    path: str = ""
    line: int = 0
    body: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line, "body": self.body}

    @classmethod
    def from_dict(cls, value: object) -> ReviewComment:
        raw = opt_dict(value)
        return cls(
            path=as_str(raw.get("path")),
            line=as_int(raw.get("line")),
            body=as_str(raw.get("body")),
        )


@dataclass(frozen=True, slots=True)
class ReviewCheck:
    name: str = ""
    ok: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}

    @classmethod
    def from_dict(cls, value: object) -> ReviewCheck:
        raw = opt_dict(value)
        return cls(
            name=as_str(raw.get("name")),
            ok=as_bool(raw.get("ok"), True),
            detail=as_str(raw.get("detail")),
        )


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    decision: str = REQUEST_CHANGES
    summary: str = ""
    comments: tuple[ReviewComment, ...] = ()
    checks: tuple[ReviewCheck, ...] = ()

    @property
    def approved(self) -> bool:
        return self.decision == APPROVE

    @property
    def blocking(self) -> tuple[ReviewCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "summary": self.summary,
            "comments": [comment.to_dict() for comment in self.comments],
            "checks": [check.to_dict() for check in self.checks],
        }

    @classmethod
    def from_dict(cls, value: object) -> ReviewVerdict:
        raw = opt_dict(value)
        decision = as_str(raw.get("decision"), REQUEST_CHANGES).strip().lower()
        if decision in ("approve", "approved", "lgtm", "accept"):
            decision = APPROVE
        else:
            decision = REQUEST_CHANGES
        return cls(
            decision=decision,
            summary=as_str(raw.get("summary")),
            comments=tuple(ReviewComment.from_dict(item) for item in opt_list(raw.get("comments"))),
            checks=tuple(ReviewCheck.from_dict(item) for item in opt_list(raw.get("checks"))),
        )


class Reviewer(Protocol):
    """The reviewer's brain. The workflow depends on this, not on a transport."""

    def review(self, pr: PullRequest, diff: str, workspace: Workspace) -> ReviewVerdict: ...


_REVIEW_SYSTEM = """\
You are an autonomous code reviewer. You are reviewing a pull request against the
repository it targets. You may read files and run tests/lint, but you CANNOT edit
anything — you only judge.

Decide whether the change is correct, minimal, and safe to approve.

Check at least:
- Correctness: does the change actually do what the linked issue asked, without
  breaking other behaviour?
- Scope: is it the smallest change that works? Flag unrelated edits, new
  dependencies, and changes to CI or configuration that the issue did not require.
- Tests: are the new/changed behaviours covered, and do the existing tests pass?

Rules:
- Ground every comment in the diff or in code you actually read; cite the file
  (and line where possible).
- `decision` is "approve" only when nothing blocking remains. Otherwise
  "request_changes". Be strict: when in doubt, request changes.
- When done, call `submit_review`. Do not write prose outside the tool call.
"""


class LlmReviewer:
    """A :class:`Reviewer` backed by a chat model running a read-only tool-loop."""

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

    def review(self, pr: PullRequest, diff: str, workspace: Workspace) -> ReviewVerdict:
        tools = [
            *workspace_tools(writable=False),
            fn(
                "submit_review",
                "Submit the review verdict. Call this to finish.",
                {
                    "decision": _STRING,
                    "summary": _STRING,
                    "comments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"path": _STRING, "line": _INTEGER, "body": _STRING},
                            "required": ["body"],
                        },
                    },
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": _STRING, "ok": _BOOL, "detail": _STRING},
                            "required": ["name", "ok"],
                        },
                    },
                },
                ["decision", "summary", "checks"],
            ),
        ]
        user = (
            f"Pull request #{pr.number}: {pr.title}\n"
            f"Base: {pr.base}  Head: {pr.head}\n\n"
            f"Description:\n{pr.body.strip()}\n\n"
            f"Diff:\n{diff.strip() or '(empty diff)'}"
        )
        args = run_coding_loop(
            self._client,
            self._settings,
            system=_REVIEW_SYSTEM,
            user=user,
            tools=tools,
            terminal="submit_review",
            workspace=workspace,
            max_rounds=self._budget.review,
        )
        return ReviewVerdict.from_dict(args)


@dataclass(frozen=True, slots=True)
class ScriptedReviewer:
    """A :class:`Reviewer` returning a fixed verdict — for tests and the eval suite."""

    verdict: ReviewVerdict = ReviewVerdict()

    def review(self, pr: PullRequest, diff: str, workspace: Workspace) -> ReviewVerdict:
        return self.verdict
