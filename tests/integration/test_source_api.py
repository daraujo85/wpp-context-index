"""Integration tests for T22: GET /api/v1/source/{context_id} and
GET /api/v1/source/media/{message_id}. Real local Qdrant (throwaway
collection "wpp_context" reused since source.py hardcodes it, same as
search.py/qdrant.py -- cleaned up per-test). wpp.get_media is stubbed with
a small synthetic file, per the task's own test spec.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from app.adapters.qdrant import build_payload, point_id, upsert
from app.adapters.wpp import WppMediaNotFoundError
from app.domain.contexts import ContextUnit, deterministic_id
from app.domain.guardrail import GuardrailResult
from app.main import app

_COLLECTION = "wpp_context"
_VECTOR_SIZE = 768  # nomic-embed-text dimension (was 1024 for mxbai-embed-large)

client = TestClient(app)


@pytest.fixture
def qdrant(monkeypatch):
    # app.config.Settings.qdrant_url defaults to "http://qdrant:6333" (the
    # in-docker-network hostname); the endpoint reads it fresh per-request,
    # so pointing it at the host-reachable address here is enough for
    # TestClient (no fixture teardown needed elsewhere).
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    c = QdrantClient(url="http://127.0.0.1:6333", check_compatibility=False)
    if not c.collection_exists(_COLLECTION):
        c.create_collection(
            _COLLECTION, vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE)
        )
    yield c


def _fixture_unit_and_guardrail():
    message_ids = ["false_1", "false_2"]
    unit = ContextUnit(
        id=deterministic_id("acme", "1203@g.us", message_ids, "t22"),
        company="acme",
        chat_id="1203@g.us",
        message_ids=message_ids,
    )
    guardrail = GuardrailResult(
        decision="index",
        work_relevance=0.9,
        contains_personal_content=False,
        title="Erro 500 ao alterar vencimento",
        summary="No grupo Operação foi reportado erro 500.",
        kind="bug",
        topics=["vencimento", "cobranca"],
    )
    return unit, guardrail


def test_get_source_returns_chat_messages_and_media(qdrant):
    unit, guardrail = _fixture_unit_and_guardrail()
    media = [{"message_id": "false_1", "type": "image", "description": "tela com erro 500"}]
    upsert(unit, guardrail, [0.1] * _VECTOR_SIZE, collection_name=_COLLECTION, client=qdrant, media=media)
    context_id = point_id(unit)

    resp = client.get(f"/api/v1/source/{context_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["chat"] == {"id": "1203@g.us"}
    assert body["messages"] == [{"message_id": "false_1"}, {"message_id": "false_2"}]
    assert body["media"] == [{"message_id": "false_1", "fetch_url": "/api/v1/source/media/false_1"}]

    qdrant.delete(_COLLECTION, points_selector=[point_id(unit)])


def test_get_source_unknown_id_returns_404(qdrant):
    resp = client.get("/api/v1/source/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_get_source_malformed_id_returns_404_not_500(qdrant):
    resp = client.get("/api/v1/source/not-a-uuid")
    assert resp.status_code == 404


def test_get_source_media_streams_bytes_and_deletes_temp_file():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"fake-png-bytes")
        tmp_path = f.name

    with patch("app.api.source.wpp.get_media", return_value=(tmp_path, "image/png", 14)):
        resp = client.get("/api/v1/source/media/true_1_ABC")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"fake-png-bytes"
    assert not Path(tmp_path).exists()


def test_get_source_media_missing_id_returns_404():
    with patch("app.api.source.wpp.get_media", side_effect=WppMediaNotFoundError("no media")):
        resp = client.get("/api/v1/source/media/truncated-id")
    assert resp.status_code == 404
