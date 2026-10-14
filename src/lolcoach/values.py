"""Safe accessors for untyped parsed data (OP.GG output, YAML loads).

`typed_repr.parse_typed` and ``yaml.safe_load`` both return plain ``object``
trees. These helpers narrow them to expected shapes without sprinkling ``Any``
through the codebase, and degrade to defaults instead of raising on a missing
optional field (a key absence should not abort a multi-hour fetch or a load).
"""

from __future__ import annotations

from typing import cast


def as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    raise TypeError(f"expected object, got {type(value).__name__}")


def opt_dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return cast("list[object]", value)
    raise TypeError(f"expected list, got {type(value).__name__}")


def opt_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def opt_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
