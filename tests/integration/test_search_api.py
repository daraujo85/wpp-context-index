"""Integration tests for POST /api/v1/search (T15). services.search.search
is mocked — no live Qdrant/embedding model required (same pattern as
tests/integration/test_health.py)."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.search import SearchResult

client = TestClient(app)

_EXPECTED_KEYS = {
    "id", "score", "title", "summary", "kind", "chat_name", "senders",
    "first_timestamp", "source_message_ids", "media_types", "urls",
    "source_url",
}


def test_search_returns_prd_11_4_shape():
    fake_results = [
        SearchResult(
            context_id="ctx_abc", score=0.87, title="Mockup da tela de cancelamento",
            summary="Fulano enviou o prototipo...", chat_id="produto@g.us",
            topics=["produto"],
        ),
    ]
    with patch("app.api.search.search", return_value=fake_results):
        resp = client.post("/api/v1/search", json={"query": "mockup cancelamento"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "mockup cancelamento"
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert set(result.keys()) == _EXPECTED_KEYS
    assert result["id"] == "ctx_abc"
    assert result["score"] == 0.87
    assert result["title"] == "Mockup da tela de cancelamento"
    assert result["summary"] == "Fulano enviou o prototipo..."
    assert result["source_url"] == "/api/v1/source/ctx_abc"


def test_search_uses_defaults_and_passes_query_through():
    with patch("app.api.search.search", return_value=[]) as mock_search:
        resp = client.post("/api/v1/search", json={"query": "q"})

    assert resp.status_code == 200
    assert resp.json() == {"query": "q", "results": []}
    mock_search.assert_called_once_with("q", filters=None, limit=10)


def test_search_passes_filters_and_limit_through():
    with patch("app.api.search.search", return_value=[]) as mock_search:
        resp = client.post(
            "/api/v1/search",
            json={"query": "q", "filters": {"chat_id": "x@g.us"}, "limit": 3},
        )

    assert resp.status_code == 200
    mock_search.assert_called_once_with("q", filters={"chat_id": "x@g.us"}, limit=3)
