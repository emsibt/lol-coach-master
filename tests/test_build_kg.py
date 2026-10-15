"""Tests for snapshot -> knowledge-graph generation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lolcoach.dataprovider import store
from lolcoach.dataprovider.build_kg import build_knowledge_graph
from lolcoach.knowledge.ontology import load_ontology
from lolcoach.knowledge.reference import load_reference


def _seed_champions(conn: sqlite3.Connection) -> None:
    store.upsert_champion(conn, 1, "Annie", "Annie", "annie")
    store.upsert_champion(conn, 3, "Galio", "Galio", "galio")
    store.upsert_champion_stats(
        conn, 3, "mid", play=50000, win_rate=0.51, pick_rate=0.05, ban_rate=0.02,
        tier=2, rank=10,
    )


def test_build_and_reload(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    _seed_champions(conn)
    store.upsert_matchup(
        conn, 3, 1, "mid", play=1500, my_win_rate=0.57, counter_win_rate=0.43, kind="strong"
    )
    store.upsert_synergy(conn, 3, 1, "mid", play=1200, win_rate=0.54)
    conn.commit()

    seed = tmp_path / "seed"
    counts = build_knowledge_graph(conn, seed, patch="14.20")
    assert counts == {
        "champion_count": 2,
        "relation_count": 2,
        "counter_count": 1,
        "synergy_count": 1,
    }

    ontology = load_ontology(
        seed / "champions.yaml", seed / "relations.yaml", seed / "patches.yaml"
    )
    assert set(ontology.champions) == {"annie", "galio"}
    assert ontology.patch == "14.20"
    counters = [r for r in ontology.relations if r.type == "counter"]
    assert len(counters) == 1
    assert counters[0].source == "galio"
    assert counters[0].target == "annie"
    assert counters[0].games == 1500
    conn.close()


def test_weak_matchup_derives_winner_direction(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    _seed_champions(conn)
    # Galio's own win rate is below even => Annie is the winner.
    store.upsert_matchup(
        conn, 3, 1, "mid", play=1500, my_win_rate=0.45, counter_win_rate=0.55, kind="weak"
    )
    conn.commit()

    seed = tmp_path / "seed"
    build_knowledge_graph(conn, seed)
    ontology = load_ontology(
        seed / "champions.yaml", seed / "relations.yaml", seed / "patches.yaml"
    )
    counters = [r for r in ontology.relations if r.type == "counter"]
    assert counters[0].source == "annie"
    assert counters[0].target == "galio"
    conn.close()


def test_low_sample_edges_are_dropped(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    _seed_champions(conn)
    store.upsert_matchup(
        conn, 3, 1, "mid", play=50, my_win_rate=0.70, counter_win_rate=0.30, kind="strong"
    )
    conn.commit()

    seed = tmp_path / "seed"
    counts = build_knowledge_graph(conn, seed)
    assert counts["relation_count"] == 0
    conn.close()


def test_reference_files_are_always_written(tmp_path: Path) -> None:
    """The loader needs a stable path, so the files exist even with no rows."""
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    _seed_champions(conn)
    conn.commit()

    seed = tmp_path / "seed"
    counts = build_knowledge_graph(conn, seed)
    assert counts == {
        "champion_count": 2,
        "relation_count": 0,
        "counter_count": 0,
        "synergy_count": 0,
    }
    for name in ("reference.yaml", "builds.yaml", "items.yaml"):
        assert (seed / name).exists()
    assert load_reference(seed).is_empty is True
    conn.close()


def test_reference_and_builds_roundtrip(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    _seed_champions(conn)
    store.upsert_champion_reference(
        conn, 3, lore="lore", blurb="blurb", tags=["Mage"], ally_tips=["a"], enemy_tips=["e"],
        abilities=[{"slot": "P", "name": "Passive", "description": "d"}], updated_at="now",
    )
    store.upsert_champion_build(conn, 3, "mid", "core", 0, item_id=6655, item_name="Luden's",
                                play=100, win=60, pick_rate=0.3)
    store.upsert_champion_build(conn, 3, "mid", "skill", 0, item_name="Q")
    store.upsert_champion_build(conn, 3, "mid", "skill", 1, item_name="E")
    store.upsert_champion_rune(conn, 3, "mid", "Sorcery", 0, "Summon Aery")
    store.upsert_champion_rune(conn, 3, "mid", "Resolve", 100, "Bone Plating")
    store.upsert_champion_rune(conn, 3, "mid", "Stat shards", 200, "Adaptive Force")
    store.upsert_item(conn, 6655, name="Luden's Companion", description="dmg", gold_total=3000,
                      builds_into=[6653], builds_from=[1056])
    conn.commit()

    seed = tmp_path / "seed"
    build_knowledge_graph(conn, seed)
    reference = load_reference(seed)

    galio = reference.champion("galio")
    assert galio is not None
    assert galio.name == "Galio"
    assert galio.ally_tips == ("a",)
    assert [ability.slot for ability in galio.abilities] == ["P"]

    build = reference.build("galio", "mid")
    assert build is not None
    assert build.skill_order == ("Q", "E")
    assert [group.item_type for group in build.item_sets] == ["core"]
    assert build.item_sets[0].names == ("Luden's",)
    assert build.item_sets[0].win_rate == 0.6
    # Page order survives the round trip: primary, secondary, then stat shards.
    assert [page.page for page in build.runes] == ["Sorcery", "Resolve", "Stat shards"]
    assert build.runes[1].picks == ("Bone Plating",)

    item = reference.item(6655)
    assert item is not None
    assert item.builds_into == (6653,)
    assert item.builds_from == (1056,)
    conn.close()
