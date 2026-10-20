"""GitHub domain types — the shapes the agent workflows exchange.

Plain frozen dataclasses with explicit ``to_dict``/``from_dict`` so a value can
travel through the workflow executor's JSON-safe execution data and be rendered
by the Trace Inspector. Nothing here imports an HTTP or CLI client.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lolcoach.values import as_bool, as_int, as_str, opt_dict, opt_list

# The three review events GitHub accepts (mirrors the REST API's ``event``).
APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
COMMENT = "COMMENT"


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    body: str = ""
    labels: tuple[str, ...] = ()
    state: str = "open"
    url: str = ""
    author: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "labels": list(self.labels),
            "state": self.state,
            "url": self.url,
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, value: object) -> Issue:
        raw = opt_dict(value)
        return cls(
            number=as_int(raw.get("number")),
            title=as_str(raw.get("title")),
            body=as_str(raw.get("body")),
            labels=tuple(_strings(raw.get("labels"))),
            state=as_str(raw.get("state"), "open"),
            url=as_str(raw.get("url")),
            author=as_str(raw.get("author")),
        )


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    title: str
    body: str = ""
    head: str = ""
    base: str = ""
    state: str = "open"
    url: str = ""
    draft: bool = False
    merged: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "head": self.head,
            "base": self.base,
            "state": self.state,
            "url": self.url,
            "draft": self.draft,
            "merged": self.merged,
        }

    @classmethod
    def from_dict(cls, value: object) -> PullRequest:
        raw = opt_dict(value)
        return cls(
            number=as_int(raw.get("number")),
            title=as_str(raw.get("title")),
            body=as_str(raw.get("body")),
            head=as_str(raw.get("head")),
            base=as_str(raw.get("base")),
            state=as_str(raw.get("state"), "open"),
            url=as_str(raw.get("url")),
            draft=as_bool(raw.get("draft")),
            merged=as_bool(raw.get("merged")),
        )


@dataclass(frozen=True, slots=True)
class Review:
    number: int
    event: str
    body: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"number": self.number, "event": self.event, "body": self.body}


@dataclass(frozen=True, slots=True)
class PullFile:
    """One file changed by a pull request (from the diff's file list)."""

    path: str
    additions: int = 0
    deletions: int = 0
    change_type: str = "modified"


@dataclass(frozen=True, slots=True)
class RepoRef:
    """The repository a client targets (``owner/name``)."""

    owner: str
    name: str
    default_branch: str = "main"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    def to_dict(self) -> dict[str, object]:
        return {"owner": self.owner, "name": self.name, "default_branch": self.default_branch}


@dataclass(frozen=True, slots=True)
class RepoStatus:
    """A snapshot of a repository's local and remote state, for guardrails."""

    branch: str
    clean: bool
    commits_behind: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def _strings(value: object) -> list[str]:
    return [as_str(item) for item in opt_list(value) if as_str(item)]
