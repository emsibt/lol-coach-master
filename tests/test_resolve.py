"""Tests for forgiving champion name resolution."""

from __future__ import annotations

from pathlib import Path

import yaml

from lolcoach.knowledge.ontology import load_ontology
from lolcoach.knowledge.resolve import NameResolver
from lolcoach.text import slugify


def _resolver(tmp_path: Path) -> NameResolver:
    names = ["Galio", "Miss Fortune", "Wukong", "Kai'Sa", "Master Yi"]
    champions = [
        {
            "slug": slugify(name),
            "name": name,
            "champion_id": index + 1,
            "key": name.replace(" ", "").replace("'", ""),
            "positions": [],
        }
        for index, name in enumerate(names)
    ]
    meta: dict[str, object] = {
        "patch": "test",
        "source": "unit",
        "generated_at": "2026-01-01T00:00:00Z",
        "champion_count": len(champions),
        "relation_count": 0,
    }
    champions_path = tmp_path / "champions.yaml"
    relations_path = tmp_path / "relations.yaml"
    patches_path = tmp_path / "patches.yaml"
    champions_path.write_text(
        yaml.safe_dump({"meta": meta, "champions": champions}), encoding="utf-8"
    )
    relations_path.write_text(
        yaml.safe_dump({"meta": meta, "relations": []}), encoding="utf-8"
    )
    patches_path.write_text(yaml.safe_dump({"meta": meta}), encoding="utf-8")
    return NameResolver(load_ontology(champions_path, relations_path, patches_path))


def test_exact_and_case_insensitive(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    assert resolver.resolve("Galio") == "galio"
    assert resolver.resolve("GALIO") == "galio"
    assert resolver.resolve("  galio ") == "galio"


def test_spacing_and_apostrophes(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    assert resolver.resolve("Miss Fortune") == "missfortune"
    assert resolver.resolve("missfortune") == "missfortune"
    assert resolver.resolve("Kai'Sa") == "kaisa"
    assert resolver.resolve("kaisa") == "kaisa"


def test_aliases(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    assert resolver.resolve("mf") == "missfortune"
    assert resolver.resolve("monkey king") == "wukong"
    assert resolver.resolve("yi") == "masteryi"


def test_unknown_returns_none(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    assert resolver.resolve("NotAChampion") is None
    assert resolver.resolve("") is None
