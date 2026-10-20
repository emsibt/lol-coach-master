"""An in-memory :class:`~lolcoach.github.client.GitHubClient` for tests and eval.

The agent evaluation and the unit tests run against this fake, so they need no
network, no token, and no ``gh`` installed — the same hermetic property the
backend test suite already has. It records what it was asked to do (comments,
labels, reviews, created PRs) so a test or an eval check can assert on the
*effects* of an agent run, not only on its recorded output.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from lolcoach.github.client import GitHubError
from lolcoach.github.types import (
    Issue,
    PullFile,
    PullRequest,
    Review,
)


@dataclass
class FakeGitHubClient:
    """A scriptable GitHub. Seed it, run an agent, inspect what it did."""

    issues: dict[int, Issue] = field(default_factory=dict)
    pull_requests: dict[int, PullRequest] = field(default_factory=dict)
    diffs: dict[int, str] = field(default_factory=dict)
    files: dict[int, list[PullFile]] = field(default_factory=dict)
    repo_slug: str = "example/repo"

    # Effects, in call order, for assertions.
    comments: list[tuple[int, str]] = field(default_factory=list)
    labels_added: list[tuple[int, tuple[str, ...]]] = field(default_factory=list)
    labels_removed: list[tuple[int, str]] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    merged: list[int] = field(default_factory=list)
    created: list[PullRequest] = field(default_factory=list)

    _next_pr: int = 1000

    # --- seeding helpers (test/eval convenience) ---

    def seed_issue(self, issue: Issue) -> None:
        self.issues[issue.number] = issue

    def seed_pull_request(
        self, pr: PullRequest, *, diff: str = "", files: Sequence[PullFile] = ()
    ) -> None:
        self.pull_requests[pr.number] = pr
        self.diffs[pr.number] = diff
        self.files[pr.number] = list(files)

    # --- protocol ---

    def list_issues(self, *, label: str) -> list[Issue]:
        return [
            issue
            for issue in self.issues.values()
            if label in issue.labels and issue.state == "open"
        ]

    def get_issue(self, number: int) -> Issue:
        issue = self.issues.get(number)
        if issue is None:
            raise GitHubError(f"no issue #{number}")
        return issue

    def comment_issue(self, number: int, body: str) -> None:
        self.comments.append((number, body))

    def add_labels(self, number: int, labels: Sequence[str]) -> None:
        self.labels_added.append((number, tuple(labels)))
        issue = self.issues.get(number)
        if issue is not None:
            merged = list(issue.labels)
            for label in labels:
                if label not in merged:
                    merged.append(label)
            self.issues[number] = Issue(
                number=issue.number, title=issue.title, body=issue.body,
                labels=tuple(merged), state=issue.state, url=issue.url, author=issue.author,
            )

    def remove_label(self, number: int, label: str) -> None:
        self.labels_removed.append((number, label))
        issue = self.issues.get(number)
        if issue is not None:
            self.issues[number] = Issue(
                number=issue.number, title=issue.title, body=issue.body,
                labels=tuple(item for item in issue.labels if item != label),
                state=issue.state, url=issue.url, author=issue.author,
            )

    def find_pull_request(self, *, head: str) -> PullRequest | None:
        for pr in self.pull_requests.values():
            if pr.head == head and pr.state == "open":
                return pr
        return None

    def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> PullRequest:
        self._next_pr += 1
        pr = PullRequest(
            number=self._next_pr,
            title=title,
            body=body,
            head=head,
            base=base,
            state="open",
            url=f"https://github.com/{self.repo_slug}/pull/{self._next_pr}",
        )
        self.pull_requests[pr.number] = pr
        self.created.append(pr)
        return pr

    def get_pull_request(self, number: int) -> PullRequest:
        pr = self.pull_requests.get(number)
        if pr is None:
            raise GitHubError(f"no pull request #{number}")
        return pr

    def pull_request_files(self, number: int) -> list[PullFile]:
        return list(self.files.get(number, []))

    def pull_request_diff(self, number: int) -> str:
        return self.diffs.get(number, "")

    def submit_review(self, number: int, *, event: str, body: str) -> Review:
        review = Review(number=number, event=event, body=body)
        self.reviews.append(review)
        return review

    def merge_pull_request(self, number: int, *, method: str = "squash") -> bool:
        pr = self.pull_requests.get(number)
        if pr is None:
            raise GitHubError(f"no pull request #{number}")
        self.merged.append(number)
        self.pull_requests[number] = PullRequest(
            number=pr.number, title=pr.title, body=pr.body, head=pr.head, base=pr.base,
            state="merged", url=pr.url, draft=pr.draft, merged=True,
        )
        return True
