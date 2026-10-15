"""The MCP client parses both OP.GG response shapes.

Most tools return a typed-repr, but a few (``lol_list_items``) return plain JSON.
``_parse_result_text`` must accept both without changing typed-repr behavior.
"""

from __future__ import annotations

import json

from lolcoach.dataprovider.opgg_mcp import _parse_result_text
from lolcoach.values import as_dict, as_list, as_str

_TYPED = 'class R: a,b\n\nR(1,"Kai\'Sa")\n'


def test_parses_json_object() -> None:
    body = json.dumps({"data": {"items": [{"id": 3153, "name": "Blade of the Ruined King"}]}})
    root = as_dict(_parse_result_text(body))
    items = as_list(as_dict(root["data"])["items"])
    assert as_dict(items[0])["id"] == 3153


def test_parses_json_array_with_leading_whitespace() -> None:
    body = "\n  [1, 2, 3]\n"
    assert _parse_result_text(body) == [1, 2, 3]


def test_typed_repr_still_parses() -> None:
    root = as_dict(_parse_result_text(_TYPED))
    assert root["a"] == 1
    assert as_str(root["b"]) == "Kai'Sa"
