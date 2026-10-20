"""Tests for the maintenance-agent CLI commands.

``eval-agents`` is hermetic, so it runs end to end here; the ``agent-fix`` /
``agent-review`` commands are exercised at their guards (repo resolution, a git
worktree, a configured model) without ever reaching a model or GitHub.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

import lolcoach.cli as cli
from lolcoach.config import get_settings
from lolcoach.github.client import GitHubClient
from lolcoach.github.types import Issue, RepoRef


@pytest.fixture(autouse=True)
def _no_env_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the test from loading the repo's .env into os.environ process-wide."""
    monkeypatch.setattr(cli, "load_env", lambda *_args, **_kwargs: None)


@pytest.fixture()
def eval_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "eval.db"
    monkeypatch.setenv("LOL_COACH_EVAL_DB", str(path))
    return path


# --- eval-agents -----------------------------------------------------------


def test_eval_agents_runs_the_hermetic_suite(eval_db: Path) -> None:
    assert cli.main(["eval-agents"]) == 0
    assert eval_db.exists()


def test_eval_agents_writes_a_json_report(eval_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    assert cli.main(["eval-agents", "--json", str(out)]) == 0
    assert out.exists()
    assert '"kind": "agent"' in out.read_text(encoding="utf-8")


def test_eval_agents_with_a_missing_suite_reports_and_exits(
    eval_db: Path, tmp_path: Path
) -> None:
    assert cli.main(["eval-agents", "--suite", str(tmp_path / "nope.yaml")]) == 1


# --- repo / worktree resolution --------------------------------------------


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {"repo": None, "workdir": None, "issue": None, "limit": None}
    values.update(overrides)
    return argparse.Namespace(**values)


def test_resolve_repo_uses_the_flag() -> None:
    ref = cli._resolve_repo(_args(repo="owner/name"), get_settings())
    assert isinstance(ref, RepoRef)
    assert ref.slug == "owner/name"


def test_resolve_repo_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOL_COACH_GITHUB_REPO", "env/repo")
    get_settings.cache_clear()
    try:
        ref = cli._resolve_repo(_args(), get_settings())
    finally:
        get_settings.cache_clear()
    assert ref.slug == "env/repo"


def test_resolve_repo_without_configuration_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOL_COACH_GITHUB_REPO", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit):
            cli._resolve_repo(_args(), get_settings())
    finally:
        get_settings.cache_clear()


def test_worktree_refuses_a_non_checkout(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not a git checkout"):
        cli._worktree(_args(workdir=str(tmp_path)))


def test_worktree_accepts_a_checkout(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    root = cli._worktree(_args(workdir=str(tmp_path)))
    assert root == tmp_path.resolve()


# --- issue selection -------------------------------------------------------


class _GitHub:
    def __init__(self, numbers: list[int]) -> None:
        self._numbers = numbers

    def list_issues(self, *, label: str) -> list[Issue]:
        return [Issue(number=n, title=f"i{n}", labels=(label,)) for n in self._numbers]


def _github(numbers: list[int]) -> GitHubClient:
    return cast(GitHubClient, _GitHub(numbers))


def test_issue_numbers_prefers_the_named_issue() -> None:
    numbers = cli._issue_numbers(_args(issue=5), _github([1, 2, 3]), get_settings())
    assert numbers == [5]


def test_issue_numbers_lists_labelled_issues_with_a_limit() -> None:
    github = _github([1, 2, 3])
    assert cli._issue_numbers(_args(), github, get_settings()) == [1, 2, 3]
    assert cli._issue_numbers(_args(limit=2), github, get_settings()) == [1, 2]


# --- agent-fix / agent-review guards ---------------------------------------


@pytest.fixture()
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("LOL_COACH_GITHUB_REPO", "owner/name")
    monkeypatch.setenv("LOL_COACH_EXECUTIONS_DB", str(tmp_path / "executions.db"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_agent_fix_without_a_model_exits(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lolcoach.agent.llm.build_client", lambda _settings: None)
    assert cli.main(["agent-fix", "--issue", "1", "--workdir", str(checkout)]) == 1


def test_agent_review_without_a_model_exits(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lolcoach.agent.llm.build_client", lambda _settings: None)
    assert cli.main(["agent-review", "--pr", "7", "--workdir", str(checkout)]) == 1
