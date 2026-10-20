"""GitHub access for the agent loop — the only layer that talks to GitHub.

A protocol (:class:`GitHubClient`), a real ``gh``-CLI implementation, and an
in-memory fake. Mirrors ``lolcoach.dataprovider``: the rest of the app depends
on the protocol, so tests and the agent evaluation never touch the network.
"""

from lolcoach.github.client import GhCliClient, GitHubClient, GitHubError
from lolcoach.github.fake import FakeGitHubClient
from lolcoach.github.types import (
    APPROVE,
    COMMENT,
    REQUEST_CHANGES,
    Issue,
    PullFile,
    PullRequest,
    RepoRef,
    Review,
)

__all__ = [
    "APPROVE",
    "COMMENT",
    "REQUEST_CHANGES",
    "FakeGitHubClient",
    "GhCliClient",
    "GitHubClient",
    "GitHubError",
    "Issue",
    "PullFile",
    "PullRequest",
    "RepoRef",
    "Review",
]
