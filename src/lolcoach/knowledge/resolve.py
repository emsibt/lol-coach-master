"""Forgiving champion name resolution.

Draft entry is typed by a human under time pressure, so resolution is tiered:
exact name → slug → squashed name → known community alias → fuzzy match. Returns
the canonical slug or ``None``; callers decide whether an unknown is an error.
"""

from __future__ import annotations

import difflib
import logging

from lolcoach.knowledge.ontology import Ontology
from lolcoach.text import slugify, squash

logger = logging.getLogger(__name__)

# Community shorthand -> canonical slug. Only used if the slug exists in the
# loaded graph, so a wrong guess degrades to "not found" rather than a bad match.
_ALIASES: dict[str, str] = {
    "wukong": "wukong",
    "monkeyking": "wukong",
    "mundo": "drmundo",
    "drmundo": "drmundo",
    "mf": "missfortune",
    "yi": "masteryi",
    "tf": "twistedfate",
    "asol": "aureliansol",
    "nunu": "nunuwillump",
    "kat": "katarina",
    "cait": "caitlyn",
    "blitz": "blitzcrank",
    "morde": "mordekaiser",
    "voli": "volibear",
    "panth": "pantheon",
    "tahm": "tahmkench",
    "tk": "tahmkench",
    "j4": "jarvaniv",
    "fiddle": "fiddlesticks",
    "gp": "gangplank",
    "reksai": "reksai",
    "chos": "chogath",
}

_FUZZY_CUTOFF = 0.85


class NameResolver:
    """Resolves user-typed champion names to slugs in an :class:`Ontology`."""

    def __init__(self, ontology: Ontology) -> None:
        self._slugs = set(ontology.champions)
        self._by_exact: dict[str, str] = {}
        self._by_squash: dict[str, str] = {}
        for slug, champion in ontology.champions.items():
            for key in (champion.name.lower(), slug, champion.key.lower()):
                self._by_exact[key] = slug
            for key in (squash(champion.name), squash(slug), squash(champion.key)):
                self._by_squash[key] = slug

    def resolve(self, query: str) -> str | None:
        if not query or not query.strip():
            return None
        text = query.strip()
        if text.lower() in self._by_exact:
            return self._by_exact[text.lower()]
        squashed = squash(text)
        if squashed in self._by_squash:
            return self._by_squash[squashed]
        alias = _ALIASES.get(squashed)
        if alias is not None and alias in self._slugs:
            return alias
        slug = slugify(text)
        if slug in self._slugs:
            return slug
        close = difflib.get_close_matches(
            squashed, list(self._by_squash), n=1, cutoff=_FUZZY_CUTOFF
        )
        if close:
            return self._by_squash[close[0]]
        logger.debug("Champion name did not resolve", extra={"data": {"query": query}})
        return None
