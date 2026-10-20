"""Tests for the workspace and publisher seams — the fixer's guardrails.

These are the deterministic checks the docs promise live in code, not in a
prompt: paths confined to the root, secret files unreadable and unwritable, and
shell commands restricted to a read/verify allowlist (with git read-only).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lolcoach.agent.publisher import RecordingPublisher
from lolcoach.agent.workspace import (
    CommandResult,
    FakeWorkspace,
    RepoWorkspace,
    WorkspaceError,
    command_allowed,
)

# --- the command allowlist -------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q",
        "ruff check .",
        "mypy src",
        "cat README.md",
        "git status --porcelain",
        "git diff",
        "git log -1",
        "/usr/bin/python script.py",
    ],
)
def test_read_and_verify_commands_are_allowed(command: str) -> None:
    assert command_allowed(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "",
        "   ",
        "rm -rf /",
        "curl https://example.com",
        "git",
        "git push origin main",
        "git reset --hard",
        "bash -c 'rm -rf .'",
    ],
)
def test_mutating_or_unknown_commands_are_refused(command: str) -> None:
    assert command_allowed(command) is False


# --- RepoWorkspace over a real checkout ------------------------------------


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    workspace = RepoWorkspace(tmp_path)
    workspace.write("src/a.py", "print('hi')\n")

    assert workspace.read("src/a.py") == "print('hi')\n"
    assert workspace.exists("src/a.py") is True


def test_reading_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="no such file"):
        RepoWorkspace(tmp_path).read("nope.py")


def test_paths_outside_the_root_are_refused(tmp_path: Path) -> None:
    workspace = RepoWorkspace(tmp_path)
    with pytest.raises(WorkspaceError, match="escapes the workspace"):
        workspace.write("../escape.py", "x")
    assert workspace.exists("../escape.py") is False


@pytest.mark.parametrize("path", [".env", "sub/.env.local", "certs/key.pem", "id_rsa.key"])
def test_secret_files_are_refused_for_read_and_write(tmp_path: Path, path: str) -> None:
    workspace = RepoWorkspace(tmp_path)
    workspace.write("keep.txt", "x")  # a real file, so a read would otherwise work

    with pytest.raises(WorkspaceError, match="secret file"):
        workspace.write(path, "secret")
    with pytest.raises(WorkspaceError, match="secret file"):
        workspace.read(path)


def test_list_files_skips_ignored_directories(tmp_path: Path) -> None:
    workspace = RepoWorkspace(tmp_path)
    workspace.write("src/a.py", "a")
    workspace.write("__pycache__/a.pyc", "x")
    workspace.write("node_modules/dep/index.js", "x")

    assert workspace.list_files() == ["src/a.py"]


def test_run_refuses_a_disallowed_command(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="not permitted"):
        RepoWorkspace(tmp_path).run("rm -rf /")


def test_run_executes_an_allowed_command(tmp_path: Path) -> None:
    result = RepoWorkspace(tmp_path).run("echo hello")
    assert result.ok is True
    assert "hello" in result.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_changed_files_is_empty_when_git_fails(tmp_path: Path) -> None:
    # A directory that is not a git checkout: git status fails, so no claims.
    assert RepoWorkspace(tmp_path).changed_files() == []


# --- FakeWorkspace ---------------------------------------------------------


def test_fake_workspace_records_reads_writes_and_commands() -> None:
    workspace = FakeWorkspace()
    workspace.seed({"a.py": "old"})

    assert workspace.read("a.py") == "old"
    workspace.write("b.py", "new")

    assert workspace.reads == ["a.py"]
    assert [path for path, _ in workspace.writes] == ["b.py"]
    assert workspace.changed_files() == ["b.py"]


def test_fake_workspace_refuses_secrets_and_unknown_files() -> None:
    workspace = FakeWorkspace()
    with pytest.raises(WorkspaceError):
        workspace.write(".env", "x")
    with pytest.raises(WorkspaceError):
        workspace.read("missing.py")


def test_fake_workspace_runs_scripted_commands() -> None:
    workspace = FakeWorkspace()
    workspace.seed({"a.py": "x"}, [("ruff check .", CommandResult("ruff check .", 1, "", "boom"))])

    assert workspace.run("ruff check .").exit_code == 1
    assert workspace.run("mypy src").exit_code == 0  # unscripted -> success
    assert workspace.calls == ["ruff check .", "mypy src"]


def test_fake_workspace_can_fail_unknown_commands() -> None:
    workspace = FakeWorkspace(fail_unknown=True)
    assert workspace.run("mypy src").exit_code == 1


def test_fake_workspace_refuses_a_disallowed_command() -> None:
    with pytest.raises(WorkspaceError):
        FakeWorkspace().run("rm -rf /")


# --- publisher -------------------------------------------------------------


def test_recording_publisher_records_the_branch_and_message() -> None:
    publisher = RecordingPublisher()
    publisher.publish(branch="agent/issue-1", base="main", message="fix: thing")

    assert publisher.publishes == [
        {"branch": "agent/issue-1", "base": "main", "message": "fix: thing"}
    ]
