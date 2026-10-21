"""Langfuse tracing for agent runs, wired into the model seam.

A run is wrapped in :func:`trace`: it registers the run's trace-level attributes
(name, chat session, tags, metadata) and publishes a LangChain callback handler
to :func:`current_callbacks`. ``agent/llm.py`` reads those callbacks on every
model call, so the agent's ``Think`` and narration calls — and only those — are
recorded as generations under one trace. Prompt, completion, tool calls, token
usage, and cost all come from Langfuse's own LangChain instrumentation.

Optional and lazy, in the same spirit as the model client in ``agent/llm.py``:
the SDK is imported on first use, and with no credentials the module is a no-op
and the ``langfuse`` package is never imported. A tracing failure is logged and
skipped — it can never fail an agent run.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextvars import ContextVar
from types import ModuleType
from typing import Literal, Protocol, cast

from lolcoach.config import Settings

logger = logging.getLogger(__name__)

Scope = Literal["app", "agent"]

# The callbacks the currently active trace contributes. Read by agent/llm.py on
# every model call; empty whenever tracing is off, which is the common case.
_callbacks: ContextVar[tuple[object, ...]] = ContextVar("lolcoach_callbacks", default=())

# One client per scope: the app and the maintenance agents are separate Langfuse
# projects, selected by public/secret key, so they cannot share an instance.
_clients: dict[Scope, _LangfuseClient] = {}


class _SpanContext(Protocol):
    """The subset of a Langfuse span/attribute context manager we enter."""

    def __enter__(self) -> object: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...


class _LangfuseClient(Protocol):
    def start_as_current_observation(self, *, name: str, as_type: str) -> _SpanContext: ...
    def flush(self) -> None: ...


class _ClientFactory(Protocol):
    def __call__(
        self,
        *,
        public_key: str,
        secret_key: str,
        base_url: str | None,
        environment: str | None,
    ) -> _LangfuseClient: ...


class _Propagate(Protocol):
    def __call__(self, **attributes: object) -> _SpanContext: ...


class _HandlerFactory(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


def _credentials(settings: Settings, scope: Scope) -> tuple[str, str, str, str]:
    """The (public key, secret key, host, environment) for a tracing scope.

    Agent traces use their own project's keys. A missing agent host or
    environment falls back to the app's (same server / deployment label), but the
    keys never do: with no agent keys, agent tracing stays off rather than
    writing maintenance traces into the app's project.
    """
    if scope == "agent":
        return (
            settings.langfuse_agent_public_key,
            settings.langfuse_agent_secret_key,
            settings.langfuse_agent_host or settings.langfuse_host,
            settings.langfuse_agent_environment or settings.langfuse_environment,
        )
    return (
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        settings.langfuse_host,
        settings.langfuse_environment,
    )


def configured(settings: Settings, scope: Scope = "app") -> bool:
    """True when both Langfuse keys for ``scope`` are present."""
    public_key, secret_key, _, _ = _credentials(settings, scope)
    return bool(public_key and secret_key)


def current_callbacks() -> tuple[object, ...]:
    """The LangChain callbacks the active trace contributes (empty when off)."""
    return _callbacks.get()


def _import_module(name: str) -> ModuleType:
    """The dynamic-import seam (patched in tests to stub the Langfuse SDK)."""
    return importlib.import_module(name)


def _import(name: str) -> ModuleType | None:
    """Import a Langfuse module, or ``None`` when Langfuse is not installed."""
    try:
        return _import_module(name)
    except ImportError:
        logger.warning(
            "Langfuse package not installed; AI tracing disabled",
            extra={"data": {"module": name}},
        )
        return None


def _client_for(settings: Settings, module: ModuleType, scope: Scope) -> _LangfuseClient:
    """Construct the scope's one Langfuse client, which also registers it.

    Building it eagerly (rather than letting the callback handler create a
    default client from the environment) keeps Langfuse's client registry to a
    single instance per project, so the handler and the trace span share one
    configuration.
    """
    client = _clients.get(scope)
    if client is None:
        public_key, secret_key, host, environment = _credentials(settings, scope)
        factory = cast("_ClientFactory", getattr(module, "Langfuse"))  # noqa: B009 - dynamic
        client = factory(
            public_key=public_key,
            secret_key=secret_key,
            base_url=host or None,
            environment=environment or None,
        )
        _clients[scope] = client
    return client


def _handler(public_key: str) -> object:
    module = _import("langfuse.langchain")
    if module is None:
        raise ImportError("langfuse.langchain")
    factory = cast("_HandlerFactory", getattr(module, "CallbackHandler"))  # noqa: B009 - dynamic
    # Select the scope's client by key; with two projects in one process the
    # handler must not pick up whichever client was registered last.
    return factory(public_key=public_key)


@contextlib.contextmanager
def trace(
    settings: Settings,
    *,
    name: str,
    scope: Scope = "app",
    session_id: str | None = None,
    tags: Sequence[str] = (),
    metadata: Mapping[str, object] | None = None,
) -> Iterator[None]:
    """Record every model call inside as generations under one Langfuse trace.

    ``name`` labels the trace; ``scope`` selects the Langfuse project (``app``
    for the coach product, ``agent`` for the maintenance agents); ``session_id``
    groups the traces of one chat session (Langfuse's Sessions view); ``tags``
    and ``metadata`` are free-form dimensions to filter on. A no-op unless
    Langfuse is configured for that scope, and a tracing setup failure is logged
    and skipped rather than propagated.
    """
    if not configured(settings, scope):
        if scope == "agent":
            logger.warning(
                "Agent tracing is not configured; set LANGFUSE_AGENT_PUBLIC_KEY and "
                "LANGFUSE_AGENT_SECRET_KEY to send maintenance-agent traces to their "
                "own Langfuse project"
            )
        yield
        return

    module = _import("langfuse")
    if module is None:
        yield
        return

    public_key, _, _, _ = _credentials(settings, scope)
    client = _client_for(settings, module, scope)
    try:
        handler = _handler(public_key)
    except ImportError:
        logger.exception("Langfuse LangChain integration unavailable; continuing untraced")
        yield
        return

    propagate = cast("_Propagate", getattr(module, "propagate_attributes"))  # noqa: B009
    try:
        with propagate(
            trace_name=name,
            session_id=session_id,
            tags=list(tags),
            metadata=dict(metadata or {}),
        ):
            with client.start_as_current_observation(name=name, as_type="agent"):
                token = _callbacks.set((handler,))
                try:
                    yield
                finally:
                    _callbacks.reset(token)
    finally:
        client.flush()
