"""Provider-neutral chat-model client for the agent.

A single narrow seam over LangChain (Claude and OpenAI), so the rest of the
agent layer never imports an SDK and never sees a provider-specific message
shape. The only per-provider difference is how an image part is encoded; the
tool-calling interface itself is uniform (``bind_tools`` → ``invoke``).

All untyped-library handling is confined to this module: the SDKs are imported
lazily via ``importlib`` and their results narrowed before they leave here.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import uuid4

from lolcoach.config import Settings
from lolcoach.observability.tracing import current_callbacks
from lolcoach.values import as_str, opt_dict

logger = logging.getLogger(__name__)


class LlmUnavailableError(RuntimeError):
    """The LLM was required but could not be reached, or its call failed.

    Raised instead of silently degrading to a fabricated answer: a caller that
    catches this must fail loudly, not substitute a placeholder reply.
    """


@dataclass(frozen=True, slots=True)
class ImagePart:
    media_type: str
    data_b64: str


@dataclass(frozen=True, slots=True)
class LlmToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    text: str = ""
    image: ImagePart | None = None
    tool_calls: tuple[LlmToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class LlmReply:
    text: str
    tool_calls: tuple[LlmToolCall, ...]


class LlmClient(Protocol):
    """The one thing the agent needs from a model.

    ``tool_choice`` names a tool the model must call instead of choosing freely;
    it is how a loop makes the final submission round a function call rather
    than prose.
    """

    provider: str

    def complete(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[dict[str, object]],
        *,
        tool_choice: str | None = None,
    ) -> LlmReply: ...


class _Runnable(Protocol):
    def invoke(self, input: object, config: object = None) -> object: ...


class _Model(Protocol):
    def invoke(self, input: object, config: object = None) -> object: ...
    def bind_tools(self, tools: object, **kwargs: object) -> _Runnable: ...


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _parse_tool_calls(reply: object) -> tuple[LlmToolCall, ...]:
    raw = getattr(reply, "tool_calls", None)
    if not isinstance(raw, list):
        return ()
    calls: list[LlmToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = as_str(item.get("name"))
        if not name:
            continue
        arguments = opt_dict(item.get("args")) or opt_dict(item.get("arguments"))
        call_id = as_str(item.get("id")) or uuid4().hex
        calls.append(LlmToolCall(id=call_id, name=name, arguments=arguments))
    return tuple(calls)


class _LangChainClient:
    """A thin adapter over one LangChain chat model."""

    def __init__(self, provider: str, model: _Model) -> None:
        self.provider = provider
        self._model = model

    def _image_block(self, image: ImagePart) -> dict[str, object]:
        if self.provider == "openai":
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{image.data_b64}"},
            }
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image.media_type,
                "data": image.data_b64,
            },
        }

    def _tool_choice(self, name: str) -> dict[str, object]:
        if self.provider == "anthropic":
            return {"type": "tool", "name": name}
        return {"type": "function", "function": {"name": name}}

    def _to_langchain(self, messages: Sequence[LlmMessage]) -> list[object]:
        messages_module = importlib.import_module("langchain_core.messages")
        system_cls = getattr(messages_module, "SystemMessage")  # noqa: B009 - dynamic module
        human_cls = getattr(messages_module, "HumanMessage")  # noqa: B009 - dynamic module
        ai_cls = getattr(messages_module, "AIMessage")  # noqa: B009 - dynamic module
        tool_cls = getattr(messages_module, "ToolMessage")  # noqa: B009 - dynamic module

        out: list[object] = []
        for message in messages:
            if message.role == "system":
                out.append(system_cls(content=message.text))
            elif message.role == "user":
                if message.image is None:
                    out.append(human_cls(content=message.text))
                else:
                    out.append(
                        human_cls(
                            content=[
                                {"type": "text", "text": message.text},
                                self._image_block(message.image),
                            ]
                        )
                    )
            elif message.role == "assistant":
                tool_calls = [
                    {"name": call.name, "args": call.arguments, "id": call.id, "type": "tool_call"}
                    for call in message.tool_calls
                ]
                out.append(ai_cls(content=message.text, tool_calls=tool_calls))
            elif message.role == "tool":
                out.append(tool_cls(content=message.text, tool_call_id=message.tool_call_id))
        return out

    def complete(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[dict[str, object]],
        *,
        tool_choice: str | None = None,
    ) -> LlmReply:
        langchain_messages = self._to_langchain(messages)
        runnable: _Runnable | _Model
        if tools:
            bind_kwargs: dict[str, object] = {}
            if tool_choice is not None:
                bind_kwargs["tool_choice"] = self._tool_choice(tool_choice)
            runnable = self._model.bind_tools(list(tools), **bind_kwargs)
        else:
            runnable = self._model
        callbacks = current_callbacks()
        if callbacks:
            reply = runnable.invoke(
                langchain_messages, config={"callbacks": list(callbacks)}
            )
        else:
            reply = runnable.invoke(langchain_messages)
        return LlmReply(
            text=_content_to_text(getattr(reply, "content", "")),
            tool_calls=_parse_tool_calls(reply),
        )


_DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
}


def _build_langchain_client(
    module_name: str,
    class_name: str,
    provider: str,
    model_name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str],
    max_tokens: int,
) -> LlmClient | None:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        logger.warning(
            "LLM package not installed; agent runs offline",
            extra={"data": {"provider": provider, "module": module_name}},
        )
        return None
    factory = cast("Callable[..., _Model] | None", getattr(module, class_name, None))
    if factory is None:  # pragma: no cover - dependency would be broken
        return None
    model = factory(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_tokens=max_tokens,
        default_headers=dict(extra_headers),
    )
    return _LangChainClient(provider, model)


def build_client(settings: Settings) -> LlmClient | None:
    """Return a model client, or ``None`` so the agent falls back to offline.

    ``base_url`` is always passed explicitly (the configured override, else the
    provider's real API) so an ambient ``ANTHROPIC_BASE_URL`` in the shell cannot
    silently redirect the app's credentials to some other endpoint.
    """
    base_url = settings.llm_base_url
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            return None
        return _build_langchain_client(
            "langchain_anthropic", "ChatAnthropic", "anthropic", settings.llm_model,
            settings.anthropic_api_key, base_url or _DEFAULT_BASE_URLS["anthropic"],
            settings.llm_extra_headers, settings.llm_max_tokens,
        )
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return None
        return _build_langchain_client(
            "langchain_openai", "ChatOpenAI", "openai", settings.llm_model,
            settings.openai_api_key, base_url or _DEFAULT_BASE_URLS["openai"],
            settings.llm_extra_headers, settings.llm_max_tokens,
        )
    logger.warning(
        "Unknown LLM provider; agent runs offline",
        extra={"data": {"provider": settings.llm_provider}},
    )
    return None
