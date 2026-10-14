"""Small shared text helpers (slugs and name squashing)."""

from __future__ import annotations


def slugify(name: str) -> str:
    """Lowercase alphanumeric slug, e.g. ``"Kai'Sa"`` -> ``"kaisa"``."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789")


def squash(name: str) -> str:
    """Lowercase with spaces/punctuation dropped, for forgiving name matching."""
    return "".join(ch for ch in name.lower() if ch in _ALLOWED)
