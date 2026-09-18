"""Integration test for T20: app.adapters.qdrant.build_payload()'s optional
`media` param. Mirrors test_qdrant_adapter.py's fixture pattern (T12):
throwaway collection, real local Qdrant, deleted at the end.
"""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from app.adapters.qdrant import build_payload, point_id, upsert
from app.domain.contexts import ContextUnit, deterministic_id
from app.domain.guardrail import GuardrailResult

_COLLECTION = "wpp_context_test_t20"
_VECTOR_SIZE = 768  # nomic-embed-text dimension (was 1024 for mxbai-embed-large)


@pytest.fixture
def client():
    c = QdrantClient(url="http://127.0.0.1:6333", check_compatibility=False)
    if c.collection_exists(_COLLECTION):
        c.delete_collection(_COLLECTION)
    c.create_collection(
        _COLLECTION, vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE)
    )
    yield c
    c.delete_collection(_COLLECTION)


def _fixture_unit_and_guardrail():
    message_ids = ["false_1", "false_2"]
    unit = ContextUnit(
        id=deterministic_id("acme", "1203@g.us", message_ids, "v1"),
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


def test_build_payload_with_media_includes_it_verbatim():
    unit, guardrail = _fixture_unit_and_guardrail()
    media = [{"message_id": "false_1", "type": "image", "description": "tela com erro 500"}]

    payload = build_payload(unit, guardrail, media=media)

    assert payload["media"] == media


def test_build_payload_without_media_matches_pre_t20_shape():
    unit, guardrail = _fixture_unit_and_guardrail()

    payload = build_payload(unit, guardrail)

    assert "media" not in payload
    assert set(payload.keys()) == {
        "schema_version", "company", "kind", "title", "summary",
        "topics", "chat", "source",
    }


def test_upsert_with_media_payload_round_trips_from_real_qdrant(client):
    unit, guardrail = _fixture_unit_and_guardrail()
    vector = [0.1] * _VECTOR_SIZE
    media = [{"message_id": "false_1", "type": "image", "description": "tela com erro 500"}]

    client.upsert(
        collection_name=_COLLECTION,
        points=[{
            "id": point_id(unit),
            "vector": vector,
            "payload": build_payload(unit, guardrail, media=media),
        }],
    )

    point = client.retrieve(_COLLECTION, ids=[point_id(unit)], with_payload=True)[0]
    assert point.payload["media"] == media


def test_upsert_without_media_still_works_unmodified(client):
    unit, guardrail = _fixture_unit_and_guardrail()
    vector = [0.1] * _VECTOR_SIZE

    upsert(unit, guardrail, vector, collection_name=_COLLECTION, client=client)

    point = client.retrieve(_COLLECTION, ids=[point_id(unit)], with_payload=True)[0]
    assert "media" not in point.payload
