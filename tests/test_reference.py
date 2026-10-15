"""Tests for the lenient reference-data loader.

Reference data is advisory: a missing or malformed file must degrade to "no
data", never raise. That leniency is the loader's whole reason for existing, so
it is what these tests pin down.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lolcoach.knowledge import reference as reference_module
from lolcoach.knowledge.reference import load_reference


def _write(seed: Path, name: str, text: str) -> None:
    seed.mkdir(parents=True, exist_ok=True)
    (seed / name).write_text(text, encoding="utf-8")


def test_missing_files_yield_empty_without_raising(tmp_path: Path) -> None:
    data = load_reference(tmp_path / "does-not-exist")
    assert data.is_empty is True
    assert data.champion("galio") is None
    assert data.build("galio", "mid") is None
    assert data.item(6655) is None


def test_malformed_yaml_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "reference.yaml", "champions: [oops\n")
    _write(tmp_path, "builds.yaml", "- not a mapping\n")
    data = load_reference(tmp_path)
    assert data.is_empty is True


def test_malformed_file_logs_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write(tmp_path, "reference.yaml", "champions: [oops\n")
    with caplog.at_level(logging.WARNING):
        load_reference(tmp_path)
    assert any("unreadable" in record.message for record in caplog.records)


def test_round_trip(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "reference.yaml",
        """
champions:
  - slug: galio
    name: Galio
    lore: lore text
    blurb: blurb text
    tags: [Tank, Mage]
    ally_tips: [tip one]
    enemy_tips: [tip two]
    abilities:
      - {slot: P, name: Colossal Smash, description: passive}
      - {slot: Q, name: Winds of War, description: q}
""",
    )
    _write(
        tmp_path,
        "builds.yaml",
        """
builds:
  - slug: galio
    position: mid
    skill_order: [Q, E, W]
    item_sets:
      - {item_type: core, item_ids: [6655], names: ["Luden's"], win_rate: 0.54, pick_rate: 0.3}
    runes:
      - {page: Sorcery, picks: [Summon Aery]}
      - {page: Resolve, picks: [Bone Plating]}
""",
    )
    _write(
        tmp_path,
        "items.yaml",
        """
items:
  - {item_id: 6655, name: "Luden's Companion", description: dmg, gold_total: 3000,
     builds_into: [6653], builds_from: [1056]}
""",
    )
    data = load_reference(tmp_path)

    assert data.is_empty is False
    galio = data.champion("galio")
    assert galio is not None
    assert galio.tags == ("Tank", "Mage")
    assert galio.enemy_tips == ("tip two",)
    assert [ability.slot for ability in galio.abilities] == ["P", "Q"]

    build = data.build("galio", "mid")
    assert build is not None
    assert build.skill_order == ("Q", "E", "W")
    assert build.item_sets[0].win_rate == 0.54
    assert [page.page for page in build.runes] == ["Sorcery", "Resolve"]

    item = data.item(6655)
    assert item is not None
    assert item.builds_from == (1056,)
    # Unknown lookups stay quiet rather than raising.
    assert data.item(1) is None


def test_build_falls_back_to_any_position(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "builds.yaml",
        "builds:\n  - {slug: galio, position: top, skill_order: [Q]}\n",
    )
    data = load_reference(tmp_path)
    assert data.build("galio") is not None
    assert data.build("galio", "top") is not None
    assert data.build("galio", "mid") is None


def test_entries_without_a_slug_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "reference.yaml", "champions:\n  - {name: Nameless}\n")
    _write(tmp_path, "items.yaml", "items:\n  - {name: No Id}\n")
    data = load_reference(tmp_path)
    assert data.is_empty is True


def test_get_reference_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reference_module, "_SEED_DIR", tmp_path)
    reference_module.get_reference.cache_clear()
    try:
        assert reference_module.get_reference() is reference_module.get_reference()
    finally:
        reference_module.get_reference.cache_clear()
