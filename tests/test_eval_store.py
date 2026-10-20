"""Tests for the evaluation-run history store (``data/eval.db``, ``eval_runs``)."""

from __future__ import annotations

from pathlib import Path

from lolcoach.eval.store import EvalRunStore, counts_of

_REPORT: dict[str, object] = {
    "total": 3,
    "passed": 2,
    "failed": 1,
    "scenarios": [
        {"id": "a", "passed": True},
        {"id": "b", "passed": True},
        {"id": "c", "passed": False},
    ],
}


def test_record_then_list_round_trips(tmp_path: Path) -> None:
    store = EvalRunStore.connect(tmp_path / "eval.db")
    try:
        run_id = store.record(kind="agent", provider="p", model="m", report=_REPORT)
        runs = store.list_runs()
    finally:
        store.close()

    assert len(runs) == 1
    summary = runs[0]
    assert summary.run_id == run_id
    assert summary.kind == "agent"
    assert (summary.total, summary.passed, summary.failed) == (3, 2, 1)
    assert summary.ok is False
    assert summary.finished_at


def test_get_run_returns_the_report(tmp_path: Path) -> None:
    store = EvalRunStore.connect(tmp_path / "eval.db")
    try:
        run_id = store.record(kind="chat", provider="p", model="m", report=_REPORT)
        record = store.get_run(run_id)
    finally:
        store.close()

    assert record is not None
    assert record.kind == "chat"
    assert record.report["total"] == 3


def test_get_run_is_none_for_an_unknown_id(tmp_path: Path) -> None:
    store = EvalRunStore.connect(tmp_path / "eval.db")
    try:
        assert store.get_run("nope") is None
    finally:
        store.close()


def test_newest_run_comes_first(tmp_path: Path) -> None:
    store = EvalRunStore.connect(tmp_path / "eval.db")
    try:
        store.record(
            kind="agent",
            provider="p",
            model="m",
            report=_REPORT,
            started_at="2026-01-01T00:00:00Z",
        )
        second = store.record(
            kind="agent", provider="p", model="m", report=_REPORT, started_at="2026-02-01T00:00:00Z"
        )
        runs = store.list_runs()
    finally:
        store.close()

    assert runs[0].run_id == second


def test_a_passing_run_reports_ok(tmp_path: Path) -> None:
    store = EvalRunStore.connect(tmp_path / "eval.db")
    try:
        store.record(
            kind="agent",
            provider="p",
            model="m",
            report={"total": 2, "passed": 2, "failed": 0, "scenarios": []},
        )
        assert store.list_runs()[0].ok is True
    finally:
        store.close()


def test_counts_of_derives_from_scenarios_when_totals_are_absent() -> None:
    total, passed, failed = counts_of({"scenarios": [{"passed": True}, {"passed": False}]})
    assert (total, passed, failed) == (2, 1, 1)


def test_counts_of_tolerates_an_empty_report() -> None:
    assert counts_of({}) == (0, 0, 0)
