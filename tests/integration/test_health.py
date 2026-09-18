"""Integration tests for /health and /ready. Qdrant HTTP call and the WPP
adapter's `chats()` are mocked — no live Qdrant/WPP server required."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_always_200_no_dependency_call():
    with patch("app.api.health.wpp.chats") as mock_chats, \
            patch("app.api.health._check_qdrant") as mock_qdrant:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    mock_chats.assert_not_called()
    mock_qdrant.assert_not_called()


def test_ready_200_when_both_up():
    with patch("app.api.health.wpp.chats", return_value=[]), \
            patch("app.api.health._check_qdrant", return_value=None):
        resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_ready_503_when_qdrant_down():
    with patch("app.api.health.wpp.chats", return_value=[]), \
            patch(
                "app.api.health._check_qdrant",
                side_effect=RuntimeError("boom"),
            ):
        resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "qdrant" in body["reason"]


def test_ready_503_when_wpp_down():
    from app.adapters.wpp import WppError

    with patch(
        "app.api.health.wpp.chats", side_effect=WppError("no wpp")
    ), patch("app.api.health._check_qdrant", return_value=None):
        resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "wpp" in body["reason"]
