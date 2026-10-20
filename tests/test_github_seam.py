"""Tests for the GitHub seam: the domain types, the fake, and the ``gh`` client.

``lolcoach.github`` is the only layer that talks to GitHub. The fake makes the
agent workflows hermetic, so most of these tests exercise it directly; the
``GhCliClient`` tests stub only the transport (``_json``/``_run``) so parsing is
covered without a network or the real ``gh``.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from lolcoach.github.client import GhCliClient, GitHubClient, GitHubError
from lolcoach.github.fake import FakeGitHubClient
from lolcoach.github.types import (
    APPROVE,
    REQUEST_CHANGES,
    Issue,
    PullFile,
    PullRequest,
    RepoRef,
)


def _issue(number: int = 1, **overrides: object) -> Issue:
    base: dict[str, object] = {
        "number": number,
        "title": f"issue {number}",
        "body": "body",
        "labels": ("agent-fix",),
        "state": "open",
        "url": f"https://example/{number}",
        "author": "someone",
    }
    base.update(overrides)
    return Issue(**base)  # type: ignore[arg-type]


def _pr(number: int = 7, **overrides: object) -> PullRequest:
    base: dict[str, object] = {
        "number": number,
        "title": f"pr {number}",
        "body": "body",
        "head": "agent/issue-1",
        "base": "main",
        "state": "open",
    }
    base.update(overrides)
    return PullRequest(**base)  # type: ignore[arg-type]


# --- types -----------------------------------------------------------------


def test_issue_round_trips_through_a_dict() -> None:
    issue = _issue(labels=("a", "b"))
    assert Issue.from_dict(issue.to_dict()) == issue


def test_issue_from_dict_tolerates_missing_and_bad_values() -> None:
    issue = Issue.from_dict({"number": 3, "labels": ["x", 5, None]})
    assert issue.number == 3
    assert issue.labels == ("x",)
    assert issue.state == "open"
    assert Issue.from_dict(None).number == 0


def test_pull_request_round_trips_and_defaults_state() -> None:
    pr = _pr(draft=True, merged=False)
    assert PullRequest.from_dict(pr.to_dict()) == pr
    assert PullRequest.from_dict({"number": 1, "title": "t"}).state == "open"


def test_repo_ref_slug() -> None:
    assert RepoRef(owner="a", name="b").slug == "a/b"


# --- the fake --------------------------------------------------------------


def test_fake_lists_only_open_issues_carrying_the_label() -> None:
    github = FakeGitHubClient()
    github.seed_issue(_issue(1, labels=("agent-fix",)))
    github.seed_issue(_issue(2, labels=("bug",)))
    github.seed_issue(_issue(3, labels=("agent-fix",), state="closed"))

    assert [issue.number for issue in github.list_issues(label="agent-fix")] == [1]


def test_fake_get_issue_raises_for_an_unknown_number() -> None:
    with pytest.raises(GitHubError):
        FakeGitHubClient().get_issue(99)


def test_fake_add_and_remove_label_edit_the_issue() -> None:
    github = FakeGitHubClient()
    github.seed_issue(_issue(1, labels=("agent-fix",)))

    github.add_labels(1, ["in-progress"])
    github.remove_label(1, "agent-fix")

    assert github.labels_added == [(1, ("in-progress",))]
    assert github.labels_removed == [(1, "agent-fix")]
    assert github.get_issue(1).labels == ("in-progress",)
    assert github.comments == []


def test_fake_finds_an_open_pull_request_by_head() -> None:
    github = FakeGitHubClient()
    github.seed_pull_request(_pr(7, head="agent/issue-1"))

    assert github.find_pull_request(head="agent/issue-1") is not None
    assert github.find_pull_request(head="agent/issue-2") is None


def test_fake_create_pull_request_records_and_numbers_it() -> None:
    github = FakeGitHubClient()
    first = github.create_pull_request(title="t", body="b", head="agent/issue-1", base="main")
    second = github.create_pull_request(title="t2", body="b", head="agent/issue-2", base="main")

    assert second.number == first.number + 1
    assert [pr.head for pr in github.created] == ["agent/issue-1", "agent/issue-2"]
    assert "pull/" in first.url


def test_fake_submit_review_records_the_event() -> None:
    github = FakeGitHubClient()
    github.seed_pull_request(_pr(7))
    review = github.submit_review(7, event=APPROVE, body="looks good")

    assert review.event == APPROVE
    assert [r.event for r in github.reviews] == [APPROVE]


def test_fake_merge_pull_request_marks_it_merged() -> None:
    github = FakeGitHubClient()
    github.seed_pull_request(_pr(7))

    assert github.merge_pull_request(7) is True
    assert github.merged == [7]
    assert github.get_pull_request(7).merged is True
    assert github.get_pull_request(7).state == "merged"


def test_fake_satisfies_the_protocol() -> None:
    client: GitHubClient = FakeGitHubClient()
    assert client.pull_request_diff(123) == ""


# --- the gh CLI client (transport stubbed) ---------------------------------


def _gh_client() -> GhCliClient:
    return GhCliClient(RepoRef(owner="owner", name="repo"))


def test_gh_client_parses_an_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "number": 5,
        "title": "t",
        "body": "b",
        "labels": [{"name": "agent-fix"}],
        "state": "OPEN",
        "url": "u",
        "author": {"login": "octocat"},
    }
    monkeypatch.setattr(GhCliClient, "_json", lambda self, args: payload)

    issue = _gh_client().get_issue(5)

    assert issue.number == 5
    assert issue.labels == ("agent-fix",)
    assert issue.state == "open"
    assert issue.author == "octocat"


def test_gh_client_parses_a_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "number": 9,
        "title": "t",
        "headRefName": "agent/issue-1",
        "baseRefName": "main",
        "state": "OPEN",
        "isDraft": True,
        "mergedAt": None,
    }
    monkeypatch.setattr(GhCliClient, "_json", lambda self, args: payload)

    pr = _gh_client().get_pull_request(9)

    assert pr.head == "agent/issue-1"
    assert pr.base == "main"
    assert pr.draft is True
    assert pr.merged is False


def test_gh_client_find_returns_none_for_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GhCliClient, "_json", lambda self, args: [])
    assert _gh_client().find_pull_request(head="x") is None


def test_gh_client_parses_pull_request_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"files": [{"path": "src/a.py", "additions": 3, "changeType": "MODIFIED"}]}
    monkeypatch.setattr(GhCliClient, "_json", lambda self, args: payload)

    files = _gh_client().pull_request_files(1)

    assert files == [PullFile(path="src/a.py", additions=3, change_type="modified")]


def test_gh_client_submit_review_maps_the_event_to_a_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Sequence[str]] = []

    def fake_run(self: GhCliClient, args: Sequence[str]) -> str:
        seen.append(list(args))
        return ""

    monkeypatch.setattr(GhCliClient, "_run", fake_run)

    review = _gh_client().submit_review(4, event=REQUEST_CHANGES, body="no")

    assert review.event == REQUEST_CHANGES
    assert "--request-changes" in seen[0]


def test_gh_client_reports_a_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lolcoach.github.client.shutil.which", lambda _name: None)
    with pytest.raises(GitHubError, match="not found on PATH"):
        _gh_client().list_issues(label="agent-fix")


def test_gh_client_reports_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "bad thing"

    monkeypatch.setattr("lolcoach.github.client.shutil.which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        "lolcoach.github.client.subprocess.run", lambda *a, **k: _Completed()
    )
    with pytest.raises(GitHubError, match="bad thing"):
        _gh_client().get_issue(1)
