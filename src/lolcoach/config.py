"""Runtime settings, read from the environment.

The repo-root ``.env`` (gitignored) is the app's configuration source of truth:
:func:`load_env` loads it with ``override=True`` so a key you put in ``.env``
(e.g. ``ANTHROPIC_API_KEY``) is used even when a stale value happens to be set in
the surrounding shell. It is called once by each entrypoint (``main.py``,
``cli.py``) before ``get_settings()``; ``get_settings()`` itself only reads
``os.environ`` so tests can control it with ``monkeypatch``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_DEFAULT_MODELS = {"anthropic": "claude-sonnet-5", "openai": "gpt-4o"}


def _repo_root() -> Path:
    # src/lolcoach/config.py -> repo root (up 3)
    return Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> None:
    """Load the repo-root ``.env`` into ``os.environ`` (``.env`` wins).

    ``override=True`` makes ``.env`` authoritative over pre-existing environment
    variables, so the credentials you paste into ``.env`` are always the ones the
    app uses. A missing file is a no-op, in which case the ambient environment is
    used unchanged.
    """
    load_dotenv(dotenv_path=path or _repo_root() / ".env", override=True)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable process settings."""

    service: str = "lol-coach"
    log_level: str = "DEBUG"
    data_dir: Path = Path()
    snapshot_db: Path = Path()
    executions_db: Path = Path()
    opgg_mcp_url: str = "https://mcp-api.op.gg/mcp"
    fetch_interval: float = 1.2
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-5"
    llm_base_url: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    max_tool_calls: int = 8
    enable_vision: bool = True
    frontend_url: str = "http://localhost:5173"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build settings from the environment (cached for the process)."""
    root = _repo_root()
    data_dir = Path(_env("LOL_COACH_DATA_DIR", str(root / "data")))
    provider = _env("LOL_COACH_LLM_PROVIDER", "anthropic").lower()
    default_model = _DEFAULT_MODELS.get(provider, "claude-sonnet-5")
    return Settings(
        service=_env("LOL_COACH_SERVICE", "lol-coach"),
        log_level=_env("LOG_LEVEL", "DEBUG"),
        data_dir=data_dir,
        snapshot_db=Path(_env("LOL_COACH_SNAPSHOT_DB", str(data_dir / "snapshot.db"))),
        executions_db=Path(_env("LOL_COACH_EXECUTIONS_DB", str(data_dir / "executions.db"))),
        opgg_mcp_url=_env("LOL_COACH_OPGG_MCP_URL", "https://mcp-api.op.gg/mcp"),
        fetch_interval=float(_env("LOL_COACH_FETCH_INTERVAL", "1.2")),
        llm_provider=provider,
        llm_model=_env("LOL_COACH_LLM_MODEL", default_model),
        llm_base_url=_env("LOL_COACH_LLM_BASE_URL", ""),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        openai_api_key=_env("OPENAI_API_KEY", ""),
        max_tool_calls=_env_int("LOL_COACH_MAX_TOOL_CALLS", 8),
        enable_vision=_env_bool("LOL_COACH_ENABLE_VISION", True),
        frontend_url=_env("LOL_COACH_FRONTEND_URL", "http://localhost:5173"),
    )
