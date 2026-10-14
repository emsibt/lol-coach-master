"""Tests for the SQLite snapshot store."""

from __future__ import annotations

from pathlib import Path

from lolcoach.dataprovider import store


def test_roundtrip_and_counts(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)

    store.upsert_champion(conn, 3, "Galio", "Galio", "galio")
    store.upsert_champion(conn, 1, "Annie", "Annie", "annie")
    store.upsert_champion_stats(
        conn, 3, "mid", play=63923, win_rate=0.49, pick_rate=0.05, ban_rate=0.02,
        tier=4, rank=122,
    )
    store.upsert_matchup(
        conn, 3, 1, "mid", play=1500, my_win_rate=0.57, counter_win_rate=0.43, kind="strong"
    )
    store.upsert_synergy(conn, 3, 1, "mid", play=800, win_rate=0.53)
    store.mark_analysis_done(conn, 3, "mid")
    store.set_meta(conn, "patch", "14.20")
    conn.commit()

    assert store.get_meta(conn, "patch") == "14.20"
    assert store.get_meta(conn, "missing") is None
    assert store.analysis_done(conn, 3, "mid") is True
    assert store.analysis_done(conn, 3, "top") is False
    assert store.name_to_id(conn)["annie"] == 1

    counted = store.counts(conn)
    assert counted["champions"] == 2
    assert counted["matchups"] == 1
    assert counted["synergies"] == 1
    assert counted["analysis_done"] == 1
    conn.close()


def test_upsert_is_idempotent(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "snapshot.db")
    store.init_db(conn)
    for _ in range(2):
        store.upsert_matchup(
            conn, 3, 1, "mid", play=1500, my_win_rate=0.57, counter_win_rate=0.43,
            kind="strong",
        )
    conn.commit()
    assert store.counts(conn)["matchups"] == 1
    conn.close()
