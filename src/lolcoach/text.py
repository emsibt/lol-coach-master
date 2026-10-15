"""Small shared text helpers (slugs and name squashing)."""

from __future__ import annotations

import re


def slugify(name: str) -> str:
    """Lowercase alphanumeric slug, e.g. ``"Kai'Sa"`` -> ``"kaisa"``."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def upper_snake(name: str) -> str:
    """OP.GG's champion identifier form.

    Spaces become underscores and other punctuation is dropped, so ``"Twisted
    Fate"`` -> ``"TWISTED_FATE"``, ``"Cho'Gath"`` -> ``"CHOGATH"``, and ``"Dr.
    Mundo"`` -> ``"DR_MUNDO"``. Note that this is *not* the Riot key uppercased:
    Riot's key for Dr. Mundo is ``DrMundo``, which OP.GG rejects.
    """
    spaced = re.sub(r"\s+", "_", name.strip())
    return re.sub(r"[^A-Za-z0-9_]", "", spaced).upper()


_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789")


def squash(name: str) -> str:
    """Lowercase with spaces/punctuation dropped, for forgiving name matching."""
    return "".join(ch for ch in name.lower() if ch in _ALLOWED)
