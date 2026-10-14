"""Tests for the static knowledge-graph review report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import yaml

from lolcoach.knowledge.ontology import Ontology, load_ontology
from lolcoach.knowledge.report import build_payload, render_report, write_report

_DATA_RE = re.compile(
    r'<script id="kg-data" type="application/json">(.*?)</script>', re.DOTALL
)


def _champion(slug: str) -> dict[str, object]:
    return {"slug": slug, "name": slug.title(), "champion_id": 1, "key": slug, "positions": []}


def _relation(source: str, target: str, reason: str = "unit") -> dict[str, object]:
    return {
        "source": source,
        "target": target,
        "type": "counter",
        "position": "mid",
        "weight": 0.05,
        "confidence": 0.75,
        "games": 1500,
        "win_rate": 0.57,
        "reason": reason,
    }


def _ontology(tmp_path: Path, reason: str = "unit") -> Ontology:
    champions = [_champion("annie"), _champion("galio")]
    relations = [_relation("galio", "annie", reason)]
    meta: dict[str, object] = {
        "patch": "test",
        "source": "unit",
        "generated_at": "2026-01-01T00:00:00Z",
        "champion_count": len(champions),
        "relation_count": len(relations),
    }
    (tmp_path / "champions.yaml").write_text(
        yaml.safe_dump({"meta": meta, "champions": champions}), encoding="utf-8"
    )
    (tmp_path / "relations.yaml").write_text(
        yaml.safe_dump({"meta": meta, "relations": relations}), encoding="utf-8"
    )
    (tmp_path / "patches.yaml").write_text(yaml.safe_dump({"meta": meta}), encoding="utf-8")
    return load_ontology(
        tmp_path / "champions.yaml", tmp_path / "relations.yaml", tmp_path / "patches.yaml"
    )


def _embedded(html: str) -> dict[str, object]:
    match = _DATA_RE.search(html)
    assert match is not None, "embedded data block missing"
    return cast(dict[str, object], json.loads(match.group(1)))


def test_report_embeds_parsable_payload(tmp_path: Path) -> None:
    html = render_report(_ontology(tmp_path))
    assert "__KG_JSON__" not in html
    assert "<canvas id=\"canvas\"" in html
    payload = _embedded(html)
    assert payload["meta"]["champion_count"] == 2  # type: ignore[index]
    assert payload["meta"]["counter_count"] == 1  # type: ignore[index]
    assert payload["relations"][0]["reason"] == "unit"  # type: ignore[index]


def test_report_payload_escapes_script_breakout(tmp_path: Path) -> None:
    html = render_report(_ontology(tmp_path, reason="</script><b>x</b>"))
    assert "</script><b>x</b>" not in html  # raw breakout must not survive
    payload = _embedded(html)
    assert payload["relations"][0]["reason"] == "</script><b>x</b>"  # type: ignore[index]


def test_build_payload_splits_counters_and_synergies(tmp_path: Path) -> None:
    payload = build_payload(_ontology(tmp_path), title="t")
    meta = payload["meta"]
    assert isinstance(meta, dict)
    assert meta["counter_count"] + meta["synergy_count"] == meta["relation_count"] == 1


def test_write_report_creates_file(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "report.html"
    write_report(_ontology(tmp_path), out)
    assert out.exists() and "Annie" in out.read_text(encoding="utf-8")
