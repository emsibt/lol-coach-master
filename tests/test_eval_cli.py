"""Tests for the `eval` CLI command's wiring (no model, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach import cli


@pytest.fixture(autouse=True)
def _no_env_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the test from loading the repo's .env into os.environ process-wide."""
    monkeypatch.setattr(cli, "load_env", lambda *_args, **_kwargs: None)


def test_eval_without_a_model_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lolcoach.agent.llm.build_client", lambda _settings: None)
    assert cli.main(["eval"]) == 1


def test_eval_with_a_missing_suite_reports_and_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A truthy stand-in client so the command reaches the scenario load.
    monkeypatch.setattr("lolcoach.agent.llm.build_client", lambda _settings: object())
    missing = tmp_path / "nope.yaml"
    assert cli.main(["eval", "--suite", str(missing)]) == 1
