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
        }, "_field_diagnostics": {"unmatched_fields": [], "hint": ""}}
    return {"data": {"summary": {}, "strong_counters": [], "weak_counters": [], "synergies": {}},
            "_field_diagnostics": {"unmatched_fields": [], "hint": ""}}


class FakeCaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        if name == "lol_list_champions":
            return _ROSTER
        if name == "lol_list_lane_meta_champions":
            return _MID_META
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


def test_fetch_is_resumable(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    fetch_snapshot(FakeCaller(), conn, interval=0.0, sleep=_noop_sleep)
    second = fetch_snapshot(FakeCaller(), conn, interval=0.0, sleep=_noop_sleep)
    assert second.analyzed_pairs == 0
    assert second.skipped_pairs == 2  # Galio[mid], Annie[mid]
    conn.close()
