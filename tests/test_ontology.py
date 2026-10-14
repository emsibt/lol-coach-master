"""Tests for knowledge-graph loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lolcoach.knowledge.ontology import OntologyError, load_ontology


def _champion(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "name": slug.title(),
        "champion_id": 1,
        "key": slug,
        "positions": [],
    }


def _write(
    tmp_path: Path,
    champions: list[dict[str, object]],
    relations: list[dict[str, object]],
) -> tuple[Path, Path, Path]:
    meta: dict[str, object] = {
        "patch": "test",
        "source": "unit",
        "generated_at": "2026-01-01T00:00:00Z",
        "champion_count": len(champions),
        "relation_count": len(relations),
    }
    champions_path = tmp_path / "champions.yaml"
    relations_path = tmp_path / "relations.yaml"
    patches_path = tmp_path / "patches.yaml"
    champions_path.write_text(
        yaml.safe_dump({"meta": meta, "champions": champions}), encoding="utf-8"
    )
    relations_path.write_text(
        yaml.safe_dump({"meta": meta, "relations": relations}), encoding="utf-8"
    )
    patches_path.write_text(yaml.safe_dump({"meta": meta}), encoding="utf-8")
    return champions_path, relations_path, patches_path


def _relation(source: str, target: str) -> dict[str, object]:
    return {
        "source": source,
        "target": target,
        "type": "counter",
        "position": "mid",
        "weight": 0.05,
        "confidence": 0.75,
        "games": 1500,
        "win_rate": 0.57,
        "reason": "unit",
    }


def test_valid_graph_loads(tmp_path: Path) -> None:
    paths = _write(tmp_path, [_champion("annie"), _champion("galio")],
                   [_relation("galio", "annie")])
    ontology = load_ontology(*paths)
    assert ontology.champion("galio") is not None
    assert ontology.counters_from["galio"][0].target == "annie"
    assert ontology.synergies_with == {}


def test_dangling_relation_is_rejected(tmp_path: Path) -> None:
    paths = _write(tmp_path, [_champion("annie")], [_relation("annie", "ghost")])
    with pytest.raises(OntologyError):
        load_ontology(*paths)


def test_schema_violation_is_rejected(tmp_path: Path) -> None:
    # champion missing required fields
    paths = _write(tmp_path, [{"slug": "annie"}], [])
    with pytest.raises(OntologyError):
        load_ontology(*paths)


def test_self_relation_is_rejected(tmp_path: Path) -> None:
    paths = _write(tmp_path, [_champion("annie")], [_relation("annie", "annie")])
    with pytest.raises(OntologyError):
        load_ontology(*paths)
