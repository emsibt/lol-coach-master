"""Load, validate, and expose the YAML knowledge graph.

``load_ontology`` assembles ``champions.yaml`` + ``relations.yaml`` +
``patches.yaml``, validates the result against ``schema/ontology.schema.json``,
runs referential integrity, and returns a frozen :class:`Ontology`. Validation
failures are hard errors — a graph with a dangling relation must not reach the
engine.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from lolcoach.constants import LANES, RELATION_TYPES
from lolcoach.values import as_float, as_int, as_str, opt_dict, opt_int, opt_list

logger = logging.getLogger(__name__)

_SEED_DIR = Path(__file__).parent / "seed"
_SCHEMA_PATH = Path(__file__).parent / "schema" / "ontology.schema.json"


class OntologyError(RuntimeError):
    """Raised when the knowledge graph fails schema or referential validation."""


@dataclass(frozen=True, slots=True)
class PositionInfo:
    position: str
    play: int
    win_rate: float
    pick_rate: float
    ban_rate: float
    tier: int | None
    rank: int | None
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Champion:
    slug: str
    name: str
    champion_id: int
    key: str
    positions: tuple[PositionInfo, ...]


@dataclass(frozen=True, slots=True)
class Relation:
    source: str
    target: str
    type: str
    position: str
    weight: float
    confidence: float
    games: int
    win_rate: float
    reason: str


@dataclass(frozen=True, slots=True)
class Ontology:
    patch: str
    generated_at: str
    source: str
    champions: dict[str, Champion]
    relations: tuple[Relation, ...]
    counters_from: dict[str, tuple[Relation, ...]]
    synergies_with: dict[str, tuple[Relation, ...]]

    def champion(self, slug: str) -> Champion | None:
        return self.champions.get(slug)


def _read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        raise OntologyError(f"knowledge graph file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data: object = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise OntologyError(f"{path} did not contain a mapping")
    return {str(k): v for k, v in data.items()}


def _validate_schema(assembled: dict[str, object]) -> None:
    schema: object = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, (dict, bool)):
        raise OntologyError("ontology schema file is not a JSON object")
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(assembled), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise OntologyError(f"knowledge graph schema violation at {location}: {first.message}")


def load_ontology(
    champions_path: Path | None = None,
    relations_path: Path | None = None,
    patches_path: Path | None = None,
) -> Ontology:
    champions_doc = _read_yaml(champions_path or _SEED_DIR / "champions.yaml")
    relations_doc = _read_yaml(relations_path or _SEED_DIR / "relations.yaml")
    patches_doc = _read_yaml(patches_path or _SEED_DIR / "patches.yaml")

    meta = {
        **opt_dict(champions_doc.get("meta")),
        **opt_dict(relations_doc.get("meta")),
        **opt_dict(patches_doc.get("meta")),
    }
    assembled: dict[str, object] = {
        "meta": meta,
        "champions": opt_list(champions_doc.get("champions")),
        "relations": opt_list(relations_doc.get("relations")),
    }
    _validate_schema(assembled)

    champions = _build_champions(opt_list(assembled["champions"]))
    relations = _build_relations(opt_list(assembled["relations"]))
    _check_referential_integrity(champions, relations)

    counters_from: dict[str, list[Relation]] = {}
    synergies_with: dict[str, list[Relation]] = {}
    for relation in relations:
        target = counters_from if relation.type == "counter" else synergies_with
        target.setdefault(relation.source, []).append(relation)

    ontology = Ontology(
        patch=as_str(meta.get("patch"), "unknown"),
        generated_at=as_str(meta.get("generated_at")),
        source=as_str(meta.get("source"), "unknown"),
        champions=champions,
        relations=tuple(relations),
        counters_from={k: tuple(v) for k, v in counters_from.items()},
        synergies_with={k: tuple(v) for k, v in synergies_with.items()},
    )
    logger.info(
        "Knowledge graph loaded",
        extra={"data": {"champions": len(champions), "relations": len(relations),
                        "patch": ontology.patch, "source": ontology.source}},
    )
    return ontology


def _build_champions(raw: list[object]) -> dict[str, Champion]:
    champions: dict[str, Champion] = {}
    for item in raw:
        d = opt_dict(item)
        slug = as_str(d.get("slug"))
        positions = tuple(
            PositionInfo(
                position=as_str(p.get("position")),
                play=as_int(p.get("play")),
                win_rate=as_float(p.get("win_rate")),
                pick_rate=as_float(p.get("pick_rate")),
                ban_rate=as_float(p.get("ban_rate")),
                tier=opt_int(p.get("tier")),
                rank=opt_int(p.get("rank")),
                roles=tuple(as_str(r) for r in opt_list(p.get("roles"))),
            )
            for p in (opt_dict(x) for x in opt_list(d.get("positions")))
        )
        champions[slug] = Champion(
            slug=slug,
            name=as_str(d.get("name"), slug),
            champion_id=as_int(d.get("champion_id"), -1),
            key=as_str(d.get("key")),
            positions=positions,
        )
    return champions


def _build_relations(raw: list[object]) -> list[Relation]:
    relations: list[Relation] = []
    for item in raw:
        d = opt_dict(item)
        relations.append(
            Relation(
                source=as_str(d.get("source")),
                target=as_str(d.get("target")),
                type=as_str(d.get("type")),
                position=as_str(d.get("position")),
                weight=as_float(d.get("weight")),
                confidence=as_float(d.get("confidence")),
                games=as_int(d.get("games")),
                win_rate=as_float(d.get("win_rate")),
                reason=as_str(d.get("reason")),
            )
        )
    return relations


def _check_referential_integrity(
    champions: dict[str, Champion], relations: list[Relation]
) -> None:
    problems: list[str] = []
    for relation in relations:
        if relation.source not in champions:
            problems.append(f"relation source {relation.source!r} is not a known champion")
        if relation.target not in champions:
            problems.append(f"relation target {relation.target!r} is not a known champion")
        if relation.source == relation.target:
            problems.append(f"relation {relation.source!r} points at itself")
        if relation.type not in RELATION_TYPES:
            problems.append(f"relation type {relation.type!r} is invalid")
        if relation.position not in LANES:
            problems.append(f"relation position {relation.position!r} is invalid")
    if problems:
        preview = "; ".join(problems[:5])
        raise OntologyError(
            f"referential integrity failed ({len(problems)} problem(s)): {preview}"
        )


@lru_cache(maxsize=1)
def get_ontology() -> Ontology:
    """Load the checked-in knowledge graph (cached for the process)."""
    return load_ontology()
