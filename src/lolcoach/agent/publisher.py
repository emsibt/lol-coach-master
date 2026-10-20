"""The publish seam: turn a workspace's changes into a pushed branch.

Committing and pushing are deliberately *not* tools the model can call — the
workspace allowlist admits only read-only git. The workflow publishes through
this seam instead, so the model can change files but can never push, force-push,
or touch the base branch. Tests and the eval suite drive a
:class:`RecordingPublisher`, which records the branch and message with no git.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class PublisherError(RuntimeError):
    """The change could not be published (nothing to commit, git failure)."""


class RepoPublisher(Protocol):
    def publish(self, *, branch: str, base: str, message: str) -> None: ...


class GitPublisher:
    """Commits the working tree to ``branch`` off ``base`` and pushes it."""

    def __init__(
        self,
        root: Path,
        *,
        remote: str = "origin",
        author_name: str = "lol-coach agent",
        author_email: str = "agent@lolcoach.local",
        timeout: float = 180.0,
    ) -> None:
        self._root = root
        self._remote = remote
        self._author_name = author_name
        self._author_email = author_email
        self._timeout = timeout

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(self._root),
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=False,
        )

    def publish(self, *, branch: str, base: str, message: str) -> None:
        # A fresh branch off base keeps the agent off main and off any long-lived
        # branch; -B recreates it if a stale one exists.
        checkout = self._git("checkout", "-B", branch, base)
        if checkout.returncode != 0:
            raise PublisherError(f"git checkout failed: {checkout.stderr.strip()}")
        add = self._git("add", "-A")
        if add.returncode != 0:
            raise PublisherError(f"git add failed: {add.stderr.strip()}")
        commit = self._git(
            "-c", f"user.name={self._author_name}",
            "-c", f"user.email={self._author_email}",
            "commit", "-m", message,
        )
        if commit.returncode != 0:
            raise PublisherError(f"git commit failed (nothing to commit?): {commit.stdout.strip()}")
        push = self._git("push", "-u", self._remote, branch)
        if push.returncode != 0:
            raise PublisherError(f"git push failed: {push.stderr.strip()}")
        logger.info("Published branch", extra={"data": {"branch": branch, "base": base}})


@dataclass
class RecordingPublisher:
    """A :class:`RepoPublisher` that records calls — for tests and the eval suite."""

    publishes: list[dict[str, str]] = field(default_factory=list)

    def publish(self, *, branch: str, base: str, message: str) -> None:
        self.publishes.append({"branch": branch, "base": base, "message": message})
