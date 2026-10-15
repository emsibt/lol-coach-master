"""Tests for the optional Langfuse tracing seam.

Two load-bearing behaviours: with no credentials tracing is a pure no-op (the
SDK is never touched), and with credentials the run's callbacks are published to
the model seam and the trace is flushed. A tracing failure must never break a
run, so the SDK and its LangChain integration are stubbed.
"""

from __future__ import annotations

from types import ModuleType
from typing import ClassVar

import pytest

from lolcoach.agent import llm
from lolcoach.agent.llm import LlmMessage, _LangChainClient
from lolcoach.config import Settings
from lolcoach.observability import tracing


def _module(name: str, **attrs: object) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class _FakeSpan:
    def __enter__(self) -> _FakeSpan:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.spans: list[tuple[str, str]] = []
        self.flushes = 0

    def start_as_current_observation(self, *, name: str, as_type: str) -> _FakeSpan:
        self.spans.append((name, as_type))
        return _FakeSpan()

    def flush(self) -> None:
        self.flushes += 1


class _StubSdk:
    """Stands in for the langfuse module tree, recording what tracing asks of it.

    ``missing`` names modules whose import should fail, to prove the seam
    degrades gracefully when the SDK (or its LangChain integration) is absent.
    """

    def __init__(self) -> None:
        self.client = _FakeClient()
        self.attributes: list[dict[str, object]] = []
        self.missing: set[str] = set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
        def langfuse(**kwargs: object) -> _FakeClient:
            self.client.kwargs = kwargs
            return self.client

        def propagate(**kwargs: object) -> _FakeSpan:
            self.attributes.append(kwargs)
            return _FakeSpan()

        def fake_import(name: str) -> ModuleType:
            if name in self.missing:
                raise ImportError(name)
            if name == "langfuse":
                return _module(name, Langfuse=langfuse, propagate_attributes=propagate)
            if name == "langfuse.langchain":
                return _module(name, CallbackHandler=lambda **kwargs: "handler")
            raise ImportError(name)

        monkeypatch.setattr(tracing, "_import_module", fake_import)
        monkeypatch.setattr(tracing, "_client", None)
        return self.client


def _settings() -> Settings:
    return Settings(
        langfuse_public_key="pk-lf-local",
        langfuse_secret_key="sk-lf-local",
        langfuse_host="http://localhost:3000",
    )


def test_configured_requires_both_keys() -> None:
    assert not tracing.configured(Settings())
    assert not tracing.configured(Settings(langfuse_public_key="pk"))
    assert not tracing.configured(Settings(langfuse_secret_key="sk"))
    assert tracing.configured(_settings())


def test_trace_is_a_noop_without_credentials() -> None:
    ran = False
    with tracing.trace(Settings(), name="agent-chat"):
        assert tracing.current_callbacks() == ()
        ran = True

    assert ran
    assert tracing.current_callbacks() == ()


def test_trace_publishes_callbacks_and_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSdk()
    client = stub.install(monkeypatch)

    with tracing.trace(
        _settings(),
        name="agent-chat",
        session_id="session-1",
        tags=("agent-chat", "openai"),
        metadata={"model": "gpt-4o"},
    ):
        assert tracing.current_callbacks() == ("handler",)

    assert tracing.current_callbacks() == ()
    assert client.spans == [("agent-chat", "agent")]
    assert client.flushes == 1
    assert stub.attributes == [
        {
            "trace_name": "agent-chat",
            "session_id": "session-1",
            "tags": ["agent-chat", "openai"],
            "metadata": {"model": "gpt-4o"},
        }
    ]
    assert client.kwargs["base_url"] == "http://localhost:3000"
    assert client.kwargs["public_key"] == "pk-lf-local"


def test_callbacks_are_reset_even_when_the_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSdk()
    client = stub.install(monkeypatch)

    with pytest.raises(RuntimeError), tracing.trace(_settings(), name="agent-chat"):
        raise RuntimeError("boom")

    assert tracing.current_callbacks() == ()
    assert client.flushes == 1  # a partial trace is still shipped


def test_trace_degrades_when_langfuse_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSdk()
    stub.missing.add("langfuse")
    stub.install(monkeypatch)

    ran = False
    with tracing.trace(_settings(), name="agent-chat"):
        ran = True

    assert ran
    assert tracing.current_callbacks() == ()


def test_trace_degrades_when_the_langchain_integration_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubSdk()
    stub.missing.add("langfuse.langchain")
    client = stub.install(monkeypatch)

    ran = False
    with tracing.trace(_settings(), name="agent-chat"):
        assert tracing.current_callbacks() == ()
        ran = True

    assert ran
    assert client.flushes == 0  # no trace was opened


class _Reply:
    content: ClassVar[str] = "ok"
    tool_calls: ClassVar[list[object]] = []


class _Recorder:
    def __init__(self) -> None:
        self.configs: list[object] = []

    def invoke(self, input: object, config: object = None) -> object:
        self.configs.append(config)
        return _Reply()


class _FakeModel:
    def __init__(self) -> None:
        self.recorder = _Recorder()

    def invoke(self, input: object, config: object = None) -> object:
        return self.recorder.invoke(input, config)

    def bind_tools(self, tools: object) -> _Recorder:
        return self.recorder


def test_model_call_forwards_the_active_trace_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeModel()
    monkeypatch.setattr(llm, "current_callbacks", lambda: ("handler",))

    _LangChainClient("openai", model).complete(
        [LlmMessage(role="user", text="hi")], [{"type": "function"}]
    )

    assert model.recorder.configs == [{"callbacks": ["handler"]}]


def test_model_call_omits_config_when_tracing_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _FakeModel()
    monkeypatch.setattr(llm, "current_callbacks", lambda: ())

    _LangChainClient("openai", model).complete([LlmMessage(role="user", text="hi")], [])

    assert model.recorder.configs == [None]
