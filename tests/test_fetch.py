"""Tests for the fetch pipeline using a fake MCP caller (no network)."""

from __future__ import annotations

from pathlib import Path

from lolcoach.dataprovider import store
from lolcoach.dataprovider.fetch import fetch_snapshot

_ROSTER = {"data": {"champions": [
    {"champion_id": 3, "key": "Galio", "name": "Galio"},
    {"champion_id": 1, "key": "Annie", "name": "Annie"},
    {"champion_id": 60003, "key": "Jade_Galio", "name": "Galio"},  # variant: excluded
]}}

_MID_META = {"data": {"positions": {"mid": [
    {"champion": "Galio", "play": 50000, "win_rate": 0.51, "pick_rate": 0.05,
     "ban_rate": 0.02, "tier": 2, "rank": 10},
    {"champion": "Annie", "play": 40000, "win_rate": 0.5, "pick_rate": 0.04,
     "ban_rate": 0.01, "tier": 2, "rank": 20},
]}}}


def _analysis(arguments: dict[str, object]) -> dict[str, object]:
    if arguments["champion"] == "GALIO":
        return {"data": {
            "summary": {"average_stats": {"win_rate": 0.49, "play": 63923},
                        "positions": [{"name": "MID", "roles": [{"name": "MAGE"}]}]},
            "strong_counters": [
                {"champion_name": "Annie", "counter_win_rate": 0.43,
                 "my_win_rate": 0.57, "play": 1500}
            ],
            "weak_counters": [],
            "synergies": {"top": [
                {"synergy_champion_name": "Annie", "win_rate": 0.54, "play": 1200}
            ]},
            "core_items": {"ids": [6655, 3157], "ids_names": ["Luden's Companion", "Zhonya's"],
                           "play": 1000, "win": 540, "pick_rate": 0.3},
            "boots": {"ids": [3020], "ids_names": ["Sorcerer's Shoes"], "play": 1000, "win": 500,
                      "pick_rate": 0.8},
            "runes": {"primary_page_name": "Sorcery",
                      "primary_rune_names": ["Summon Aery"],
                      "secondary_page_name": "Resolve",
                      "secondary_rune_names": ["Bone Plating"]},
            "skill_masteries": {"builds": [{"order": ["Q", "E", "W"], "play": 900}]},
        }, "_field_diagnostics": {"unmatched_fields": [], "hint": ""}}
    return {"data": {"summary": {}, "strong_counters": [], "weak_counters": [], "synergies": {}},
            "_field_diagnostics": {"unmatched_fields": [], "hint": ""}}


def _details(arguments: dict[str, object]) -> dict[str, object]:
    ids = {"GALIO": 3, "ANNIE": 1}
    champions = []
    for name in arguments["champions"]:
        champions.append({
            "id": ids[str(name)],
            "name": str(name).title(),
            "lore": f"{name} lore",
            "blurb": f"{name} blurb",
            "tags": ["Mage"],
            "ally_tips": [f"Play safe as {name}"],
            "enemy_tips": [f"Punish {name}'s cooldowns"],
            "passive": {"name": f"{name} Passive", "description": "passive text"},
            "spells": [{"key": "Q", "name": f"{name} Q", "description": "q text"}],
        })
    return {"data": {"champions": champions}}


_ITEMS = {"data": {"items": [
    {"item_id": 6655, "name": "Luden's Companion", "description": "dmg",
     "gold_total": 3000, "into_items": [6653], "from_items": [1056]},
]}}


class FakeCaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        if name == "lol_list_champions":
            return _ROSTER
        if name == "lol_list_lane_meta_champions":
            return _MID_META
        if name == "lol_list_champion_details":
            return _details(arguments)
        if name == "lol_list_items":
            return _ITEMS
        return _analysis(arguments)


def _noop_sleep(_seconds: float) -> None:
    return None


def test_fetch_stores_stats_matchups_synergies(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    report = fetch_snapshot(FakeCaller(), conn, interval=0.0, sleep=_noop_sleep)

    assert report.champions == 2  # Jade variant excluded
    counted = store.counts(conn)
    assert counted["champions"] == 2
    assert counted["champion_stats"] == 2
    assert counted["matchups"] == 1
    assert counted["synergies"] == 1
    assert store.get_meta(conn, "source") == "opgg-mcp"
    conn.close()


def test_fetch_stores_reference_builds_and_items(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    report = fetch_snapshot(FakeCaller(), conn, interval=0.0, sleep=_noop_sleep)

    # Both champions are recorded, even the one whose analysis carried no build.
    assert report.reference_rows == 2
    counted = store.counts(conn)
    assert counted["champion_reference"] == 2
    assert counted["items"] == 1
    assert counted["champion_builds"] > 0
    assert counted["champion_runes"] > 0

    galio_id = store.name_to_id(conn)["galio"]
    row = conn.execute(
        "SELECT * FROM champion_reference WHERE champion_id = ?", (galio_id,)
    ).fetchone()
    assert row is not None
    assert row["lore"] == "GALIO lore"
    assert store.decode_list(row["ally_tips"]) == ["Play safe as GALIO"]
    abilities = store.decode_list(row["abilities"])
    assert [ability["slot"] for ability in abilities] == ["P", "Q"]

    item = conn.execute("SELECT * FROM items WHERE item_id = 6655").fetchone()
    assert item is not None and item["name"] == "Luden's Companion"
    conn.close()


def test_fetch_is_resumable(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    fetch_snapshot(FakeCaller(), conn, interval=0.0, sleep=_noop_sleep)
    second = fetch_snapshot(FakeCaller(), conn, interval=0.0, sleep=_noop_sleep)
    assert second.analyzed_pairs == 0
    assert second.skipped_pairs == 2  # Galio[mid], Annie[mid]
    conn.close()


def test_fetch_skips_done_reference(tmp_path: Path) -> None:
    """A second run must not re-request champion details it already has."""
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    fetch_snapshot(FakeCaller(), conn, interval=0.0, sleep=_noop_sleep)
    caller = FakeCaller()
    second = fetch_snapshot(caller, conn, interval=0.0, sleep=_noop_sleep)
    assert second.reference_rows == 0
    assert [name for name, _args in caller.calls if name == "lol_list_champion_details"] == []
    conn.close()
