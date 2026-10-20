"""The workspace seam: the files and commands a coding agent may touch.

The fixer agent investigates and edits a checkout. Rather than let task bodies
call ``open``/``subprocess`` directly, they go through a :class:`Workspace`, so
the same workflow runs against a real checkout in production and against an
in-memory tree in tests and the agent evaluation — hermetically, with no disk
and no shell.

Guardrails live here, not in the model's prompt: paths are confined to the
workspace root, secret files are unreadable and unwritable, and shell commands
are restricted to an allowlist of read/verify programs. A prompt can be talked
out of its rules; a resolved-path check cannot.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Reads and writes are refused outright for these names, anywhere in the tree.
_SECRET_NAMES = (".env", ".env.local", ".env.production")
_SECRET_SUFFIXES = (".pem", ".key")
_IGNORED_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".mypy_cache",
     ".pytest_cache", ".ruff_cache"}
)
# A first token that may run; git is allowed only for read-only subcommands.
_ALLOWED_PROGRAMS = frozenset(
    {"python", "python3", "pytest", "ruff", "mypy", "cat", "ls", "grep", "find", "head",
     "tail", "wc", "echo"}
)
_ALLOWED_GIT_SUBCOMMANDS = frozenset({"status", "diff", "log", "show", "branch", "rev-parse"})

_MAX_LIST = 2000


class WorkspaceError(RuntimeError):
    """A workspace operation was refused (bad path, denied file, blocked command)."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class Workspace(Protocol):
    """The file and command surface a coding agent operates on."""

    def read(self, path: str) -> str: ...
    def write(self, path: str, content: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def list_files(self, prefix: str = "") -> list[str]: ...
    def run(self, command: str) -> CommandResult: ...
    def changed_files(self) -> list[str]: ...


def _is_secret(path: str) -> bool:
    name = Path(path).name
    return (
        name in _SECRET_NAMES
        or name.startswith(".env.")
        or name.endswith(_SECRET_SUFFIXES)
    )


def command_allowed(command: str) -> bool:
    """Whether ``command`` may run: an allowlisted program, or a read-only git call."""
    parts = command.strip().split()
    if not parts:
        return False
    program = Path(parts[0]).name
    if program == "git":
        return len(parts) >= 2 and parts[1] in _ALLOWED_GIT_SUBCOMMANDS
    return program in _ALLOWED_PROGRAMS


class RepoWorkspace:
    """A :class:`Workspace` over a real checkout rooted at ``root``."""

    def __init__(self, root: Path, *, timeout: float = 300.0) -> None:
        self._root = root.resolve()
        self._timeout = timeout

    def _resolve(self, path: str) -> Path:
        candidate = (self._root / path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise WorkspaceError(f"path escapes the workspace: {path!r}")
        if _is_secret(path):
            raise WorkspaceError(f"refusing to touch a secret file: {path!r}")
        return candidate

    def read(self, path: str) -> str:
        resolved = self._resolve(path)
        if not resolved.is_file():
            raise WorkspaceError(f"no such file: {path!r}")
        return resolved.read_text(encoding="utf-8", errors="replace")

    def write(self, path: str, content: str) -> None:
        resolved = self._resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.info("workspace write", extra={"data": {"path": path, "bytes": len(content)}})

    def exists(self, path: str) -> bool:
        try:
            return self._resolve(path).exists()
        except WorkspaceError:
            return False

    def list_files(self, prefix: str = "") -> list[str]:
        base = self._root / prefix
        out: list[str] = []
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _IGNORED_DIRS for part in path.relative_to(self._root).parts):
                continue
            out.append(path.relative_to(self._root).as_posix())
            if len(out) >= _MAX_LIST:
                break
        return out

    def run(self, command: str) -> CommandResult:
        if not command_allowed(command):
            raise WorkspaceError(f"command not permitted by the workspace policy: {command!r}")
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - environment dependent
            return CommandResult(command, 124, "", f"timed out after {self._timeout}s: {exc}")
        return CommandResult(
            command,
            completed.returncode,
            completed.stdout[-8000:],
            completed.stderr[-8000:],
        )

    def changed_files(self) -> list[str]:
        """Working-tree changes, straight from git — not from the model's claims."""
        result = self.run("git status --porcelain")
        if not result.ok:
            return []
        paths: list[str] = []
        for line in result.stdout.splitlines():
            entry = line[3:].strip()
            if not entry:
                continue
            # A rename shows as "old -> new"; the new path is what changed.
            paths.append(entry.split(" -> ")[-1].strip().strip('"'))
        return sorted(set(paths))


@dataclass
class FakeWorkspace:
    """An in-memory :class:`Workspace` for tests and the agent evaluation.

    ``scripts`` maps a command to the result it should return; an unknown command
    returns exit 0 with empty output unless ``fail_unknown`` is set. Writes and
    reads are recorded so an eval check can assert what the agent changed.
    """

    files: dict[str, str] = field(default_factory=dict)
    scripts: dict[str, CommandResult] = field(default_factory=dict)
    fail_unknown: bool = False

    reads: list[str] = field(default_factory=list)
    writes: list[tuple[str, str]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def read(self, path: str) -> str:
        self.reads.append(path)
        if _is_secret(path):
            raise WorkspaceError(f"refusing to touch a secret file: {path!r}")
        if path not in self.files:
            raise WorkspaceError(f"no such file: {path!r}")
        return self.files[path]

    def write(self, path: str, content: str) -> None:
        if _is_secret(path):
            raise WorkspaceError(f"refusing to touch a secret file: {path!r}")
        self.writes.append((path, content))
        self.files[path] = content

    def exists(self, path: str) -> bool:
        return path in self.files

    def list_files(self, prefix: str = "") -> list[str]:
        return sorted(path for path in self.files if path.startswith(prefix))

    def run(self, command: str) -> CommandResult:
        self.calls.append(command)
        if not command_allowed(command):
            raise WorkspaceError(f"command not permitted by the workspace policy: {command!r}")
        if command in self.scripts:
            return self.scripts[command]
        return CommandResult(command, 1 if self.fail_unknown else 0, "", "")

    def changed_files(self) -> list[str]:
        return sorted({path for path, _ in self.writes})

    def seed(
        self, files: dict[str, str], scripts: Sequence[tuple[str, CommandResult]] = ()
    ) -> None:
        self.files.update(files)
        for command, result in scripts:
            self.scripts[command] = result
