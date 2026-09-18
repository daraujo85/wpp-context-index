"""Integration tests for POST /api/v1/ingest and GET /api/v1/ingest/{run_id}
(T15). services.ingestion.run is mocked — no live WPP/Qdrant required
(same pattern as tests/integration/test_health.py). TestClient runs
BackgroundTasks synchronously before the POST call returns, so the GET
right after already reflects the final status."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.ingestion import IngestionSummary

client = TestClient(app)


def test_ingest_triggers_run_and_status_reflects_completion():
    fake_summary = IngestionSummary(run_id="internal-1", messages_read=5, contexts_indexed=2)
    with patch("app.api.ingest.ingestion.run", return_value=fake_summary) as mock_run:
        resp = client.post(
            "/api/v1/ingest",
            json={"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    run_id = body["run_id"]
    mock_run.assert_called_once_with("2026-01-01T00:00:00", "2026-01-02T00:00:00")

    status_resp = client.get(f"/api/v1/ingest/{run_id}")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["status"] == "completed"
    assert status_body["summary"]["messages_read"] == 5
    assert status_body["summary"]["contexts_indexed"] == 2


def test_ingest_status_reflects_failure():
    with patch("app.api.ingest.ingestion.run", side_effect=RuntimeError("boom")):
        resp = client.post(
            "/api/v1/ingest",
            json={"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"},
        )
    run_id = resp.json()["run_id"]

    status_resp = client.get(f"/api/v1/ingest/{run_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "failed"
    assert "boom" in body["error"]


def test_ingest_status_404_for_unknown_run_id():
    resp = client.get("/api/v1/ingest/does-not-exist")
    assert resp.status_code == 404
