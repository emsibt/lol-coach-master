"""Tests for the /api/admin surface: the maintenance agents' runs and eval history.

The admin routes back a surface that is deliberately separate from the coach app:
they serve the agent-loop executions and the evaluation-run history, and the eval
executions are looked up in the eval database rather than the app's.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lolcoach.config import get_settings
from lolcoach.eval.store import EvalRunStore
from lolcoach.server.app import create_app
from lolcoach.workflow.store import ExecutionStore


@pytest.fixture()
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    executions = tmp_path / "executions.db"
    evals = tmp_path / "eval.db"
    monkeypatch.setenv("LOL_COACH_EXECUTIONS_DB", str(executions))
    monkeypatch.setenv("LOL_COACH_EVAL_DB", str(evals))
    get_settings.cache_clear()
    yield executions, evals
    get_settings.cache_clear()


@pytest.fixture()
def client(paths: tuple[Path, Path]) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _seed_execution(path: Path, execution_id: str, workflow: str, *, started_at: str) -> None:
    store = ExecutionStore.connect(path)
    try:
        store.create_execution(execution_id, workflow, "1", started_at, {"issue_number": 1})
        store.finish(execution_id, "succeeded", started_at, {"ok": True}, None)
    finally:
        store.close()


def test_admin_lists_only_agent_workflows(client: TestClient, paths: tuple[Path, Path]) -> None:
    executions, _ = paths
    _seed_execution(executions, "fix-1", "issue-fix", started_at="2026-01-01T00:00:00Z")
    _seed_execution(executions, "review-1", "pr-review", started_at="2026-01-02T00:00:00Z")
    _seed_execution(executions, "chat-1", "agent-chat", started_at="2026-01-03T00:00:00Z")

    body = client.get("/api/admin/executions").json()

    ids = [row["execution_id"] for row in body]
    assert ids == ["review-1", "fix-1"]  # newest first, no chat
    assert "chat-1" not in ids


def test_admin_filters_by_workflow(client: TestClient, paths: tuple[Path, Path]) -> None:
    executions, _ = paths
    _seed_execution(executions, "fix-1", "issue-fix", started_at="2026-01-01T00:00:00Z")
    _seed_execution(executions, "review-1", "pr-review", started_at="2026-01-02T00:00:00Z")

    body = client.get("/api/admin/executions", params={"workflow": "issue-fix"}).json()
    assert [row["execution_id"] for row in body] == ["fix-1"]


def test_an_unknown_workflow_filter_falls_back_to_all_agents(
    client: TestClient, paths: tuple[Path, Path]
) -> None:
    executions, _ = paths
    _seed_execution(executions, "fix-1", "issue-fix", started_at="2026-01-01T00:00:00Z")

    body = client.get("/api/admin/executions", params={"workflow": "nope"}).json()
    assert [row["execution_id"] for row in body] == ["fix-1"]


def test_admin_execution_detail_carries_the_graph(
    client: TestClient, paths: tuple[Path, Path]
) -> None:
    executions, _ = paths
    _seed_execution(executions, "fix-1", "issue-fix", started_at="2026-01-01T00:00:00Z")

    body = client.get("/api/admin/executions/fix-1").json()

    assert body["execution_id"] == "fix-1"
    assert body["workflow"] == "issue-fix"
    assert body["graph"]["name"] == "issue-fix"
    assert "events" in body


def test_admin_execution_falls_back_to_the_eval_database(
    client: TestClient, paths: tuple[Path, Path]
) -> None:
    _, evals = paths
    _seed_execution(evals, "eval-run-1", "agent-chat", started_at="2026-01-01T00:00:00Z")

    body = client.get("/api/admin/executions/eval-run-1").json()
    assert body["execution_id"] == "eval-run-1"


def test_an_unknown_admin_execution_is_404(client: TestClient) -> None:
    assert client.get("/api/admin/executions/missing").status_code == 404


def test_the_app_execution_route_does_not_see_the_eval_database(
    client: TestClient, paths: tuple[Path, Path]
) -> None:
    _, evals = paths
    _seed_execution(evals, "eval-run-1", "agent-chat", started_at="2026-01-01T00:00:00Z")

    # Only the admin route drills into the eval DB; the app route is executions-only.
    assert client.get("/api/executions/eval-run-1").status_code == 404


def test_admin_lists_evaluation_runs(client: TestClient, paths: tuple[Path, Path]) -> None:
    _, evals = paths
    store = EvalRunStore.connect(evals)
    try:
        store.record(
            kind="agent",
            provider="p",
            model="m",
            report={"total": 1, "passed": 1, "failed": 0, "scenarios": []},
        )
        store.record(
            kind="chat",
            provider="p",
            model="m",
            report={"total": 2, "passed": 1, "failed": 1, "scenarios": []},
        )
    finally:
        store.close()

    body = client.get("/api/admin/evals").json()
    assert len(body) == 2

    filtered = client.get("/api/admin/evals", params={"kind": "agent"}).json()
    assert [run["kind"] for run in filtered] == ["agent"]


def test_admin_eval_detail_lists_scenario_verdicts(
    client: TestClient, paths: tuple[Path, Path]
) -> None:
    _, evals = paths
    store = EvalRunStore.connect(evals)
    try:
        run_id = store.record(
            kind="agent",
            provider="p",
            model="m",
            report={
                "total": 1,
                "passed": 0,
                "failed": 1,
                "scenarios": [
                    {
                        "id": "fix-real-bug",
                        "name": "a clean fix",
                        "passed": False,
                        "execution_id": "abc",
                        "status": "failed",
                        "kind": "issue_fix",
                        "checks": [
                            {"name": "status", "ok": False, "detail": "wanted succeeded"}
                        ],
                    }
                ],
            },
        )
    finally:
        store.close()

    body = client.get(f"/api/admin/evals/{run_id}").json()

    assert body["run_id"] == run_id
    assert body["kind"] == "agent"
    (scenario,) = body["scenarios"]
    assert scenario["id"] == "fix-real-bug"
    assert scenario["checks"][0]["name"] == "status"


def test_an_unknown_eval_run_is_404(client: TestClient) -> None:
    assert client.get("/api/admin/evals/missing").status_code == 404


def test_rerunning_an_agent_execution_is_refused(
    client: TestClient, paths: tuple[Path, Path]
) -> None:
    executions, _ = paths
    _seed_execution(executions, "fix-1", "issue-fix", started_at="2026-01-01T00:00:00Z")

    response = client.post("/api/executions/fix-1/rerun")

    assert response.status_code == 400
    assert "agent-fix" in response.json()["detail"]
