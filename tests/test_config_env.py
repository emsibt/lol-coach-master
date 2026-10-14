"""Tests for configuration loading: the repo ``.env`` is the credential source.

The load-bearing behaviour: a value in ``.env`` wins over a stale value already
present in the shell, so the key a user pastes into ``.env`` is the one the app
uses.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from lolcoach.agent.llm import build_client
from lolcoach.config import Settings, get_settings, load_env


def test_dotenv_wins_over_the_ambient_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")

    load_env(env_file)

    assert os.environ["ANTHROPIC_API_KEY"] == "from-dotenv"


def test_missing_dotenv_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")

    load_env(tmp_path / "does-not-exist.env")

    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_empty_dotenv_key_clears_a_stale_shell_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-shell-value")

    load_env(env_file)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.anthropic_api_key == ""
        assert build_client(settings) is None  # offline, not a phantom call
    finally:
        get_settings.cache_clear()


def test_llm_base_url_setting_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOL_COACH_LLM_BASE_URL", "http://localhost:9999")
    get_settings.cache_clear()
    try:
        assert get_settings().llm_base_url == "http://localhost:9999"
    finally:
        get_settings.cache_clear()


def test_build_client_sends_the_configured_base_url_not_the_shells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeModel:
        def invoke(self, _input: object) -> object:  # pragma: no cover - never called
            return None

        def bind_tools(self, _tools: object) -> object:  # pragma: no cover - never called
            return self

    def _fake_chat_anthropic(**kwargs: object) -> _FakeModel:
        captured.update(kwargs)
        return _FakeModel()

    module = types.ModuleType("langchain_anthropic")
    module.ChatAnthropic = _fake_chat_anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_anthropic", module)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://shell-should-be-ignored")

    default = Settings(llm_provider="anthropic", anthropic_api_key="k", llm_model="m")
    assert build_client(default) is not None
    assert str(captured["base_url"]) == "https://api.anthropic.com"
    assert captured["api_key"] == "k"

    captured.clear()
    overridden = Settings(
        llm_provider="anthropic",
        anthropic_api_key="k",
        llm_model="m",
        llm_base_url="http://localhost:9999",
    )
    assert build_client(overridden) is not None
    assert str(captured["base_url"]) == "http://localhost:9999"
