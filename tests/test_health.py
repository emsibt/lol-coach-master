"""Tests for the HTTP layer."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lolcoach import __version__
from lolcoach.server.app import create_app


def test_health() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
