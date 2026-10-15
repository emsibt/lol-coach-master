"""OP.GG MCP client (Streamable HTTP).

A minimal JSON-RPC client for the public OP.GG MCP server
(``https://mcp-api.op.gg/mcp``). No authentication is required; the server
returns a session id in the ``Mcp-Session-Id`` response header on ``initialize``
and expects it echoed on every subsequent request.

Only this module knows the wire protocol. Callers use
:meth:`OpggMcpClient.call_tool`, which performs the ``tools/call`` exchange and
returns the tool's structured data (parsed from OP.GG's typed-repr format by
:mod:`lolcoach.dataprovider.typed_repr`).
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from lolcoach import __version__
from lolcoach.dataprovider.typed_repr import parse_typed

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2025-06-18"


def _parse_result_text(text: str) -> object:
    """Parse a tool result body.

    OP.GG returns a typed-repr for most tools, but a few (e.g. ``lol_list_items``)
    return plain JSON. A typed-repr always begins with ``class`` header lines, so a
    leading ``{``/``[`` unambiguously marks a JSON body.
    """
    if text.lstrip()[:1] in ("{", "["):
        return json.loads(text)
    return parse_typed(text)


class OpggMcpError(RuntimeError):
    """Raised for transport failures and JSON-RPC / tool-level errors."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class OpggMcpClient:
    """Synchronous MCP client with retry/backoff on transport failures."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 1.5,
    ) -> None:
        self._url = url
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._session_id: str | None = None
        self._id = 0
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> OpggMcpClient:
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- protocol ---------------------------------------------------------

    def initialize(self) -> None:
        """Open a session (``initialize`` + ``notifications/initialized``)."""
        payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lolcoach", "version": __version__},
            },
        }
        response = self._post(payload, with_session=False)
        session = response.headers.get("Mcp-Session-Id", "")
        if session:
            self._session_id = session
        body = self._parse_body(response)
        self._raise_on_error("initialize", body)
        self._notify("notifications/initialized", {})
        logger.info("MCP session initialized", extra={"data": {"has_session_id": bool(session)}})

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        """Call a tool and return its parsed structured data."""
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise OpggMcpError(f"tool {name!r} returned an error: {self._content_text(result)}")
        return _parse_result_text(self._content_text(result))

    # -- internals --------------------------------------------------------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self, *, with_session: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if with_session and self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
            headers["MCP-Protocol-Version"] = _PROTOCOL_VERSION
        return headers

    def _post(self, payload: dict[str, object], *, with_session: bool) -> httpx.Response:
        last_error: Exception = OpggMcpError("request never attempted")
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(
                    self._url, json=payload, headers=self._headers(with_session=with_session)
                )
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 500:
                    return response
                last_error = OpggMcpError(
                    f"server error {response.status_code}", code=response.status_code
                )
            if attempt < self._max_retries:
                delay = self._backoff_base**attempt
                logger.warning(
                    "MCP request failed; retrying",
                    extra={
                        "data": {
                            "attempt": attempt + 1,
                            "delay_s": round(delay, 2),
                            "error": str(last_error),
                        }
                    },
                )
                time.sleep(delay)
        raise OpggMcpError(
            f"MCP request failed after {self._max_retries + 1} attempt(s): {last_error}"
        )

    def _parse_body(self, response: httpx.Response) -> dict[str, object] | None:
        text = response.text
        if not text.strip():
            return None
        content_type = response.headers.get("Content-Type", "")
        if content_type.startswith("text/event-stream"):
            text = "".join(
                line[len("data:") :].strip()
                for line in text.splitlines()
                if line.startswith("data:")
            )
        parsed: object = json.loads(text)
        if not isinstance(parsed, dict):
            raise OpggMcpError("malformed JSON-RPC response")
        return {str(key): value for key, value in parsed.items()}

    def _request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        response = self._post(payload, with_session=True)
        body = self._parse_body(response)
        self._raise_on_error(method, body)
        assert body is not None  # _raise_on_error rejects None
        result = body.get("result")
        if not isinstance(result, dict):
            raise OpggMcpError(f"{method}: malformed result")
        return {str(key): value for key, value in result.items()}

    def _notify(self, method: str, params: dict[str, object]) -> None:
        self._post(
            {"jsonrpc": "2.0", "method": method, "params": params}, with_session=True
        )

    def _raise_on_error(self, method: str, body: dict[str, object] | None) -> None:
        if body is None:
            raise OpggMcpError(f"{method}: empty response")
        error = body.get("error")
        if error is None:
            return
        if isinstance(error, dict):
            code = error.get("code")
            message = str(error.get("message", error))
            raise OpggMcpError(
                f"{method}: {message}", code=code if isinstance(code, int) else None
            )
        raise OpggMcpError(f"{method}: {error}")

    @staticmethod
    def _content_text(result: dict[str, object]) -> str:
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    return text
        raise OpggMcpError("tool result had no text content")
