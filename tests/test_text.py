"""Tests for the shared text helpers.

``upper_snake`` exists because OP.GG's champion identifiers do not follow any
obvious rule: punctuation is dropped but spaces become underscores, so ``Cho'Gath``
is ``CHOGATH`` while ``Dr. Mundo`` is ``DR_MUNDO``. Getting this wrong silently
loses whole batches of a fetch, so it is pinned down here.
"""

from __future__ import annotations

import pytest

from lolcoach.text import slugify, squash, upper_snake


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Annie", "ANNIE"),
        ("Twisted Fate", "TWISTED_FATE"),
        ("Cho'Gath", "CHOGATH"),
        ("Kai'Sa", "KAISA"),
        ("Kha'Zix", "KHAZIX"),
        ("K'Sante", "KSANTE"),
        ("Dr. Mundo", "DR_MUNDO"),
        ("Aurelion Sol", "AURELION_SOL"),
        ("Tahm Kench", "TAHM_KENCH"),
        ("Renata Glasc", "RENATA_GLASC"),
        ("LeBlanc", "LEBLANC"),
    ],
)
def test_upper_snake_matches_the_opgg_identifier(name: str, expected: str) -> None:
    assert upper_snake(name) == expected


def test_upper_snake_is_not_the_riot_key() -> None:
    """The Riot key for Dr. Mundo is ``DrMundo``; OP.GG rejects ``DRMUNDO``."""
    assert upper_snake("DrMundo") == "DRMUNDO"
    assert upper_snake("Dr. Mundo") == "DR_MUNDO"


def test_slugify_drops_punctuation_entirely() -> None:
    assert slugify("Kai'Sa") == "kaisa"
    assert slugify("Dr. Mundo") == "drmundo"
    assert slugify("Twisted Fate") == "twistedfate"


def test_squash_is_forgiving() -> None:
    assert squash("Dr. Mundo") == "drmundo"
    assert squash("Kai'Sa") == "kaisa"
