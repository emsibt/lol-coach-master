"""Parser for OP.GG's typed-repr tool output.

The OP.GG MCP server does **not** return JSON. Every tool result is a compact,
column-ordered representation: leading ``class`` lines declare each type's field
order, and the trailing expression nests constructor calls positionally::

    class Summary: id,is_rotation,is_rip,average_stats
    class AverageStats: play,win_rate,pick_rate,ban_rate

    Summary(3,false,false,AverageStats(63923,0.49,0.05,0.02))

Values are strings (double-quoted), numbers, booleans (``true``/``false``),
nested objects (``ClassName(...)``), or lists (``[...]``).

This module turns that text into plain ``dict`` / ``list`` / scalar data. It is
deliberately strict: a constructor whose arity disagrees with its declared class
raises ``TypedReprError`` rather than silently misaligning columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADER_RE = re.compile(r"^class\s+(\w+)\s*:\s*(.*)$")
_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_CONT = _IDENT_START | set("0123456789")
_UNESCAPE = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class TypedReprError(ValueError):
    """Raised when the OP.GG tool text cannot be parsed."""


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str = ""
    value: object = None


@dataclass(frozen=True, slots=True)
class _Node:
    name: str
    args: tuple[object, ...]


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            buf: list[str] = []
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(_UNESCAPE.get(text[j + 1], text[j + 1]))
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            if j >= n:
                raise TypedReprError("unterminated string literal")
            tokens.append(_Token("string", value="".join(buf)))
            i = j + 1
            continue
        single = {"(": "lparen", ")": "rparen", "[": "lbracket", "]": "rbracket",
                  ",": "comma", ":": "colon"}
        if c in single:
            tokens.append(_Token(single[c], text=c))
            i += 1
            continue
        if c.isdigit() or (c in "+-" and i + 1 < n and text[i + 1].isdigit()):
            j = i + 1 if c in "+-" else i
            while j < n and (text[j].isdigit() or text[j] in ".eE" or
                             (text[j] in "+-" and j > i and text[j - 1] in "eE")):
                j += 1
            raw = text[i:j]
            number: object = float(raw) if any(ch in raw for ch in ".eE") else int(raw)
            tokens.append(_Token("number", value=number))
            i = j
            continue
        if c in _IDENT_START:
            j = i
            while j < n and text[j] in _IDENT_CONT:
                j += 1
            word = text[i:j]
            if word in ("true", "false"):
                tokens.append(_Token("bool", value=(word == "true")))
            else:
                tokens.append(_Token("ident", text=word))
            i = j
            continue
        raise TypedReprError(f"unexpected character {c!r} at offset {i}")
    tokens.append(_Token("eof"))
    return tokens


class _ExprParser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _advance(self) -> _Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, kind: str) -> _Token:
        token = self._advance()
        if token.kind != kind:
            raise TypedReprError(f"expected {kind}, got {token.kind!r}")
        return token

    def parse_value(self) -> object:
        token = self._peek()
        if token.kind == "ident":
            if self._tokens[self._pos + 1].kind == "lparen":
                return self._parse_object()
            self._advance()
            if token.text in ("null", "None"):
                return None
            return token.text
        if token.kind == "string":
            self._advance()
            return token.value
        if token.kind in ("number", "bool"):
            self._advance()
            return token.value
        if token.kind == "lbracket":
            self._advance()
            return self._parse_sequence("rbracket")
        raise TypedReprError(f"unexpected token {token.kind!r}")

    def _parse_object(self) -> _Node:
        name = self._advance().text
        self._expect("lparen")
        return _Node(name, tuple(self._parse_sequence("rparen")))

    def _parse_sequence(self, closer: str) -> list[object]:
        items: list[object] = []
        if self._peek().kind == closer:
            self._advance()
            return items
        while True:
            items.append(self.parse_value())
            token = self._advance()
            if token.kind == closer:
                return items
            if token.kind != "comma":
                raise TypedReprError(f"expected ',' or {closer!r}, got {token.kind!r}")

    def at_end(self) -> bool:
        return self._peek().kind == "eof"


def _resolve(value: object, fields_by_class: dict[str, list[str]]) -> object:
    if isinstance(value, _Node):
        fields = fields_by_class.get(value.name)
        if fields is None:
            raise TypedReprError(f"no header for class {value.name!r}")
        if len(fields) != len(value.args):
            raise TypedReprError(
                f"{value.name}: {len(value.args)} value(s) for {len(fields)} field(s)"
            )
        return {
            field: _resolve(arg, fields_by_class)
            for field, arg in zip(fields, value.args, strict=True)
        }
    if isinstance(value, list):
        return [_resolve(item, fields_by_class) for item in value]
    return value


def parse_typed(text: str) -> object:
    """Parse OP.GG typed-repr text into plain dict/list/scalar data."""
    lines = text.splitlines()
    fields_by_class: dict[str, list[str]] = {}
    body_start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if fields_by_class:
                body_start = index + 1
                break
            continue
        header = _HEADER_RE.match(stripped)
        if header is not None:
            raw_fields = header.group(2).strip()
            fields_by_class[header.group(1)] = [
                f.strip().removesuffix("[]") for f in raw_fields.split(",") if f.strip()
            ]
            continue
        # A non-header, non-blank line before any blank separator: body begins here.
        body_start = index
        break

    if body_start is None:
        raise TypedReprError("no body expression found")
    if not fields_by_class:
        raise TypedReprError("no 'class' headers found")

    body = "\n".join(lines[body_start:]).strip()
    parser = _ExprParser(_tokenize(body))
    node = parser.parse_value()
    if not parser.at_end():
        raise TypedReprError("trailing tokens after body expression")
    return _resolve(node, fields_by_class)
