"""Load the materialized reference data (items, tips, abilities, builds).

Reference data is what the coach can *say* about a champion — tips, abilities,
lore, and a recommended build — as opposed to the knowledge graph, which is what
the engine *decides* from. It is produced by ``dataprovider/build_kg.py`` into
``knowledge/seed/{reference,builds,items}.yaml`` and read from there at runtime.

The loader is deliberately **lenient**, and that is the whole point of its
existence: reference data is advisory, so a missing or malformed file logs a
WARNING and yields an empty :class:`ReferenceData` instead of raising. Unlike
:mod:`lolcoach.knowledge.ontology`, a missing item catalog must not stop the API
from booting or the engine from scoring a draft.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from lolcoach.values import as_float, as_int, as_str, opt_dict, opt_list

logger = logging.getLogger(__name__)

_SEED_DIR = Path(__file__).parent / "seed"


@dataclass(frozen=True, slots=True)
class Ability:
    slot: str  # "P" for the passive, else the spell key (Q/W/E/R)
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class ChampionReference:
    slug: str
    name: str
    lore: str
    blurb: str
    tags: tuple[str, ...]
    ally_tips: tuple[str, ...]
    enemy_tips: tuple[str, ...]
    abilities: tuple[Ability, ...]


@dataclass(frozen=True, slots=True)
class Item:
    item_id: int
    name: str
    description: str
    gold_total: int
    builds_into: tuple[int, ...]
    builds_from: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ItemSet:
    item_type: str  # "starter" | "boots" | "core"
    names: tuple[str, ...]
    item_ids: tuple[int, ...]
    win_rate: float
    pick_rate: float


@dataclass(frozen=True, slots=True)
class RunePage:
    page: str
    picks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChampionBuild:
    slug: str
    position: str
    skill_order: tuple[str, ...]
    item_sets: tuple[ItemSet, ...]
    runes: tuple[RunePage, ...]


@dataclass(frozen=True, slots=True)
class ReferenceData:
    champions: dict[str, ChampionReference] = field(default_factory=dict)
    builds: dict[tuple[str, str], ChampionBuild] = field(default_factory=dict)
    items: dict[int, Item] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.champions or self.builds or self.items)

    def champion(self, slug: str) -> ChampionReference | None:
        return self.champions.get(slug)

    def build(self, slug: str, position: str | None = None) -> ChampionBuild | None:
        """The build for ``slug`` in ``position``, or any of its builds if None."""
        if position:
            return self.builds.get((slug, position))
        for (build_slug, _position), build in self.builds.items():
            if build_slug == slug:
                return build
        return None

    def item(self, item_id: int) -> Item | None:
        return self.items.get(item_id)


def load_reference(seed_dir: Path | None = None) -> ReferenceData:
    """Assemble the reference data from the checked-in YAML (never raises)."""
    directory = seed_dir or _SEED_DIR
    data = ReferenceData(
        champions=_champions(_read(directory / "reference.yaml")),
        builds=_builds(_read(directory / "builds.yaml")),
        items=_items(_read(directory / "items.yaml")),
    )
    logger.info(
        "Reference data loaded",
        extra={
            "data": {
                "champions": len(data.champions),
                "builds": len(data.builds),
                "items": len(data.items),
            }
        },
    )
    return data


@lru_cache(maxsize=1)
def get_reference() -> ReferenceData:
    """Load the materialized reference data (cached for the process)."""
    return load_reference()


def _read(path: Path) -> dict[str, object]:
    if not path.exists():
        logger.warning(
            "Reference data file missing; continuing without it",
            extra={"data": {"path": str(path)}},
        )
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data: object = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        logger.exception(
            "Reference data file unreadable; continuing without it",
            extra={"data": {"path": str(path)}},
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "Reference data file is not a mapping; ignoring",
            extra={"data": {"path": str(path)}},
        )
        return {}
    return {str(key): value for key, value in data.items()}


def _strings(value: object) -> tuple[str, ...]:
    return tuple(text for text in (as_str(item) for item in opt_list(value)) if text)


def _champions(doc: dict[str, object]) -> dict[str, ChampionReference]:
    champions: dict[str, ChampionReference] = {}
    for item in opt_list(doc.get("champions")):
        d = opt_dict(item)
        slug = as_str(d.get("slug"))
        if not slug:
            continue
        champions[slug] = ChampionReference(
            slug=slug,
            name=as_str(d.get("name"), slug),
            lore=as_str(d.get("lore")),
            blurb=as_str(d.get("blurb")),
            tags=_strings(d.get("tags")),
            ally_tips=_strings(d.get("ally_tips")),
            enemy_tips=_strings(d.get("enemy_tips")),
            abilities=tuple(
                Ability(
                    slot=as_str(ability.get("slot")),
                    name=as_str(ability.get("name")),
                    description=as_str(ability.get("description")),
                )
                for ability in (opt_dict(x) for x in opt_list(d.get("abilities")))
            ),
        )
    return champions


def _builds(doc: dict[str, object]) -> dict[tuple[str, str], ChampionBuild]:
    builds: dict[tuple[str, str], ChampionBuild] = {}
    for item in opt_list(doc.get("builds")):
        d = opt_dict(item)
        slug, position = as_str(d.get("slug")), as_str(d.get("position"))
        if not slug or not position:
            continue
        builds[(slug, position)] = ChampionBuild(
            slug=slug,
            position=position,
            skill_order=_strings(d.get("skill_order")),
            item_sets=tuple(
                ItemSet(
                    item_type=as_str(group.get("item_type")),
                    names=_strings(group.get("names")),
                    item_ids=tuple(as_int(i) for i in opt_list(group.get("item_ids"))),
                    win_rate=as_float(group.get("win_rate")),
                    pick_rate=as_float(group.get("pick_rate")),
                )
                for group in (opt_dict(x) for x in opt_list(d.get("item_sets")))
            ),
            runes=tuple(
                RunePage(page=as_str(page.get("page")), picks=_strings(page.get("picks")))
                for page in (opt_dict(x) for x in opt_list(d.get("runes")))
            ),
        )
    return builds


def _items(doc: dict[str, object]) -> dict[int, Item]:
    items: dict[int, Item] = {}
    for item in opt_list(doc.get("items")):
        d = opt_dict(item)
        item_id = as_int(d.get("item_id"), -1)
        name = as_str(d.get("name"))
        if item_id < 0 or not name:
            continue
        items[item_id] = Item(
            item_id=item_id,
            name=name,
            description=as_str(d.get("description")),
            gold_total=as_int(d.get("gold_total")),
            builds_into=tuple(as_int(i) for i in opt_list(d.get("builds_into"))),
            builds_from=tuple(as_int(i) for i in opt_list(d.get("builds_from"))),
        )
    return items
