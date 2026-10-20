"""The GitHub client seam, and the ``gh``-CLI implementation.

``lolcoach.github`` is the *only* layer that talks to GitHub, mirroring how
``dataprovider`` is the only layer that talks to OP.GG. The agent workflows
depend on the :class:`GitHubClient` protocol, never on a concrete transport, so
tests and the agent evaluation drive a :class:`~lolcoach.github.fake.FakeGitHubClient`
with no network and no credentials.

The real implementation shells out to the authenticated ``gh`` CLI rather than
embedding a token: the operator's existing ``gh auth`` is the credential, and it
is never read, logged, or persisted by this process.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Sequence
from typing import Protocol, cast

from lolcoach.github.types import (
    COMMENT,
    Issue,
    PullFile,
    PullRequest,
    RepoRef,
    Review,
)
from lolcoach.values import as_int, as_str, opt_dict, opt_list

logger = logging.getLogger(__name__)

_PR_FIELDS = "number,title,body,headRefName,baseRefName,state,url,isDraft,mergedAt"
_ISSUE_FIELDS = "number,title,body,labels,state,url,author"


class GitHubError(RuntimeError):
    """A GitHub operation failed (transport error or non-zero ``gh`` exit)."""


class GitHubClient(Protocol):
    """Everything the agents need from GitHub. No method mutates local state."""

    def list_issues(self, *, label: str) -> list[Issue]: ...
    def get_issue(self, number: int) -> Issue: ...
    def comment_issue(self, number: int, body: str) -> None: ...
    def add_labels(self, number: int, labels: Sequence[str]) -> None: ...
    def remove_label(self, number: int, label: str) -> None: ...
    def find_pull_request(self, *, head: str) -> PullRequest | None: ...
    def create_pull_request(
        self, *, title: str, body: str, head: str, base: str
    ) -> PullRequest: ...
    def get_pull_request(self, number: int) -> PullRequest: ...
    def pull_request_files(self, number: int) -> list[PullFile]: ...
    def pull_request_diff(self, number: int) -> str: ...
    def submit_review(self, number: int, *, event: str, body: str) -> Review: ...
    def merge_pull_request(self, number: int, *, method: str = "squash") -> bool: ...


def _parse_labels(value: object) -> tuple[str, ...]:
    labels: list[str] = []
    for item in opt_list(value):
        if isinstance(item, str):
            labels.append(item)
        else:
            name = as_str(opt_dict(item).get("name"))
            if name:
                labels.append(name)
    return tuple(labels)


def _author(value: object) -> str:
    if isinstance(value, str):
        return value
    return as_str(opt_dict(value).get("login"))


def _issue_from(raw: object) -> Issue:
    data = opt_dict(raw)
    return Issue(
        number=as_int(data.get("number")),
        title=as_str(data.get("title")),
        body=as_str(data.get("body")),
        labels=_parse_labels(data.get("labels")),
        state=as_str(data.get("state"), "open").lower(),
        url=as_str(data.get("url")),
        author=_author(data.get("author")),
    )


def _pull_from(raw: object) -> PullRequest:
    data = opt_dict(raw)
    return PullRequest(
        number=as_int(data.get("number")),
        title=as_str(data.get("title")),
        body=as_str(data.get("body")),
        head=as_str(data.get("headRefName")),
        base=as_str(data.get("baseRefName")),
        state=as_str(data.get("state"), "open").lower(),
        url=as_str(data.get("url")),
        draft=bool(data.get("isDraft")),
        merged=bool(data.get("mergedAt")),
    )


class GhCliClient:
    """A :class:`GitHubClient` over the authenticated ``gh`` CLI."""

    def __init__(self, repo: RepoRef, *, gh: str = "gh", timeout: float = 90.0) -> None:
        self._repo = repo
        self._gh = gh
        self._timeout = timeout

    def _run(self, args: Sequence[str]) -> str:
        if shutil.which(self._gh) is None:
            raise GitHubError(
                f"the {self._gh!r} CLI was not found on PATH; install it and run `gh auth login`"
            )
        command = [self._gh, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - environment dependent
            raise GitHubError(f"gh timed out after {self._timeout}s: {' '.join(command)}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise GitHubError(f"gh exited {completed.returncode}: {detail}")
        return completed.stdout

    def _json(self, args: Sequence[str]) -> object:
        text = self._run(args)
        try:
            return cast(object, json.loads(text))
        except json.JSONDecodeError as exc:  # pragma: no cover - gh always emits JSON here
            raise GitHubError(f"gh returned non-JSON output: {text[:200]}") from exc

    def list_issues(self, *, label: str) -> list[Issue]:
        raw = self._json(
            [
                "issue", "list",
                "--repo", self._repo.slug,
                "--label", label,
                "--state", "open",
                "--limit", "50",
                "--json", _ISSUE_FIELDS,
            ]
        )
        return [_issue_from(item) for item in opt_list(raw)]

    def get_issue(self, number: int) -> Issue:
        raw = self._json(
            ["issue", "view", str(number), "--repo", self._repo.slug, "--json", _ISSUE_FIELDS]
        )
        return _issue_from(raw)

    def comment_issue(self, number: int, body: str) -> None:
        self._run(
            ["issue", "comment", str(number), "--repo", self._repo.slug, "--body", body]
        )

    def add_labels(self, number: int, labels: Sequence[str]) -> None:
        if not labels:
            return
        self._run(
            [
                "issue", "edit", str(number),
                "--repo", self._repo.slug,
                "--add-label", ",".join(labels),
            ]
        )

    def remove_label(self, number: int, label: str) -> None:
        self._run(
            ["issue", "edit", str(number), "--repo", self._repo.slug, "--remove-label", label]
        )

    def find_pull_request(self, *, head: str) -> PullRequest | None:
        raw = self._json(
            [
                "pr", "list",
                "--repo", self._repo.slug,
                "--head", head,
                "--state", "open",
                "--limit", "1",
                "--json", _PR_FIELDS,
            ]
        )
        items = opt_list(raw)
        return _pull_from(items[0]) if items else None

    def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> PullRequest:
        url = self._run(
            [
                "pr", "create",
                "--repo", self._repo.slug,
                "--title", title,
                "--body", body,
                "--head", head,
                "--base", base,
            ]
        ).strip()
        return self.get_pull_request_by_ref(url or head)

    def get_pull_request_by_ref(self, ref: str) -> PullRequest:
        raw = self._json(
            ["pr", "view", ref, "--repo", self._repo.slug, "--json", _PR_FIELDS]
        )
        return _pull_from(raw)

    def get_pull_request(self, number: int) -> PullRequest:
        return self.get_pull_request_by_ref(str(number))

    def pull_request_files(self, number: int) -> list[PullFile]:
        raw = self._json(
            [
                "pr", "view", str(number),
                "--repo", self._repo.slug,
                "--json", "files",
            ]
        )
        files: list[PullFile] = []
        for item in opt_list(opt_dict(raw).get("files")):
            data = opt_dict(item)
            path = as_str(data.get("path"))
            if path:
                files.append(
                    PullFile(
                        path=path,
                        additions=as_int(data.get("additions")),
                        deletions=as_int(data.get("deletions")),
                        change_type=as_str(data.get("changeType"), "modified").lower(),
                    )
                )
        return files

    def pull_request_diff(self, number: int) -> str:
        return self._run(["pr", "diff", str(number), "--repo", self._repo.slug])

    def submit_review(self, number: int, *, event: str, body: str) -> Review:
        flag = {
            "APPROVE": "--approve",
            "REQUEST_CHANGES": "--request-changes",
            COMMENT: "--comment",
        }.get(event, "--comment")
        self._run(["pr", "review", str(number), "--repo", self._repo.slug, flag, "--body", body])
        return Review(number=number, event=event, body=body)

    def merge_pull_request(self, number: int, *, method: str = "squash") -> bool:
        flag = {"squash": "--squash", "merge": "--merge", "rebase": "--rebase"}.get(
            method, "--squash"
        )
        self._run(["pr", "merge", str(number), "--repo", self._repo.slug, flag])
        return True
