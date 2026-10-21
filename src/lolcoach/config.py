"""Runtime settings, read from the environment.

The repo-root ``.env`` (gitignored) is the app's configuration source of truth:
:func:`load_env` loads it with ``override=True`` so a key you put in ``.env``
(e.g. ``ANTHROPIC_API_KEY``) is used even when a stale value happens to be set in
the surrounding shell. It is called once by each entrypoint (``main.py``,
``cli.py``) before ``get_settings()``; ``get_settings()`` itself only reads
``os.environ`` so tests can control it with ``monkeypatch``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

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


def _env_json_object(name: str) -> dict[str, str]:
    """Parse a JSON object env var into a ``str -> str`` map (empty if unset/bad)."""
    raw = os.environ.get(name)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ignoring malformed JSON in %s", name)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("ignoring non-object JSON in %s", name)
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple (empty/unset -> ``default``)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable process settings."""

    service: str = "lol-coach"
    log_level: str = "DEBUG"
    data_dir: Path = Path()
    snapshot_db: Path = Path()
    executions_db: Path = Path()
    chat_db: Path = Path()
    eval_db: Path = Path()
    opgg_mcp_url: str = "https://mcp-api.op.gg/mcp"
    fetch_interval: float = 1.2
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-5"
    llm_base_url: str = ""
    llm_extra_headers: dict[str, str] = field(default_factory=dict)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    max_tool_calls: int = 8
    max_questions_per_session: int = 5
    enable_vision: bool = True
    enable_live_tools: bool = True
    live_region: str = "NA"
    live_timeout: float = 15.0
    live_cache_ttl: float = 300.0
    live_min_interval: float = 0.25
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    langfuse_environment: str = ""
    # Maintenance agents trace to their own Langfuse project (own keys), so the
    # app's project holds only coach-product traces.
    langfuse_agent_public_key: str = ""
    langfuse_agent_secret_key: str = ""
    langfuse_agent_host: str = ""
    langfuse_agent_environment: str = ""
    frontend_url: str = "http://localhost:5173"
    # --- maintenance agents (issue-fix / pr-review) ---
    github_repo: str = ""
    agent_base_branch: str = "main"
    agent_issue_label: str = "agent-fix"
    agent_branch_prefix: str = "agent/issue"
    agent_loop_rounds: int = 12
    agent_verify_commands: tuple[str, ...] = (
        "ruff check .",
        "mypy src",
        "python -m pytest -q",
    )
    agent_auto_merge: bool = False
    agent_denied_paths: tuple[str, ...] = (".github/workflows", ".env")


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
        chat_db=Path(_env("LOL_COACH_CHAT_DB", str(data_dir / "chat.db"))),
        eval_db=Path(_env("LOL_COACH_EVAL_DB", str(data_dir / "eval.db"))),
        opgg_mcp_url=_env("LOL_COACH_OPGG_MCP_URL", "https://mcp-api.op.gg/mcp"),
        fetch_interval=float(_env("LOL_COACH_FETCH_INTERVAL", "1.2")),
        llm_provider=provider,
        llm_model=_env("LOL_COACH_LLM_MODEL", default_model),
        llm_base_url=_env("LOL_COACH_LLM_BASE_URL", ""),
        llm_extra_headers=_env_json_object("LOL_COACH_LLM_EXTRA_HEADERS"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        openai_api_key=_env("OPENAI_API_KEY", ""),
        max_tool_calls=_env_int("LOL_COACH_MAX_TOOL_CALLS", 8),
        max_questions_per_session=_env_int("LOL_COACH_MAX_QUESTIONS", 5),
        enable_vision=_env_bool("LOL_COACH_ENABLE_VISION", True),
        enable_live_tools=_env_bool("LOL_COACH_ENABLE_LIVE_TOOLS", True),
        live_region=_env("LOL_COACH_LIVE_REGION", "NA").upper(),
        live_timeout=float(_env("LOL_COACH_LIVE_TIMEOUT", "15")),
        live_cache_ttl=float(_env("LOL_COACH_LIVE_CACHE_TTL", "300")),
        live_min_interval=float(_env("LOL_COACH_LIVE_MIN_INTERVAL", "0.25")),
        langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=_env("LANGFUSE_SECRET_KEY", ""),
        langfuse_host=_env("LANGFUSE_HOST", ""),
        langfuse_environment=_env("LANGFUSE_TRACING_ENVIRONMENT", ""),
        langfuse_agent_public_key=_env("LANGFUSE_AGENT_PUBLIC_KEY", ""),
        langfuse_agent_secret_key=_env("LANGFUSE_AGENT_SECRET_KEY", ""),
        langfuse_agent_host=_env("LANGFUSE_AGENT_HOST", ""),
        langfuse_agent_environment=_env("LANGFUSE_AGENT_TRACING_ENVIRONMENT", ""),
        frontend_url=_env("LOL_COACH_FRONTEND_URL", "http://localhost:5173"),
        github_repo=_env("LOL_COACH_GITHUB_REPO", ""),
        agent_base_branch=_env("LOL_COACH_AGENT_BASE_BRANCH", "main"),
        agent_issue_label=_env("LOL_COACH_AGENT_ISSUE_LABEL", "agent-fix"),
        agent_branch_prefix=_env("LOL_COACH_AGENT_BRANCH_PREFIX", "agent/issue"),
        agent_loop_rounds=_env_int("LOL_COACH_AGENT_LOOP_ROUNDS", 12),
        agent_verify_commands=_env_tuple(
            "LOL_COACH_AGENT_VERIFY_COMMANDS",
            ("ruff check .", "mypy src", "python -m pytest -q"),
        ),
        agent_auto_merge=_env_bool("LOL_COACH_AGENT_AUTO_MERGE", False),
        agent_denied_paths=_env_tuple(
            "LOL_COACH_AGENT_DENIED_PATHS", (".github/workflows", ".env")
        ),
    )
