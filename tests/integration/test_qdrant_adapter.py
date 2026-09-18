"""Integration test for app.adapters.qdrant — REAL local Qdrant (docker
compose, see PROJECT setup). Embedding vector is a fixed fake (mocking
inference.embed isn't needed here since upsert() takes the vector directly)
-- this test is about upsert() idempotency, not embedding quality.

Uses a distinctly-named test-only collection (separate from "wpp_context")
and deletes it at the end so it never pollutes the shared local Qdrant used
by other tasks.
"""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from app.adapters.qdrant import point_id, upsert
from app.domain.contexts import ContextUnit, deterministic_id
from app.domain.guardrail import GuardrailResult

_COLLECTION = "wpp_context_test_t12"
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


def test_upsert_same_point_twice_does_not_duplicate(client):
    unit, guardrail = _fixture_unit_and_guardrail()
    vector = [0.1] * _VECTOR_SIZE

    upsert(unit, guardrail, vector, collection_name=_COLLECTION, client=client)
    upsert(unit, guardrail, vector, collection_name=_COLLECTION, client=client)

    count = client.count(_COLLECTION).count
    assert count == 1


def test_upsert_payload_has_no_binary_or_unexpected_fields(client):
    unit, guardrail = _fixture_unit_and_guardrail()
    vector = [0.1] * _VECTOR_SIZE

    upsert(unit, guardrail, vector, collection_name=_COLLECTION, client=client)

    point = client.retrieve(_COLLECTION, ids=[point_id(unit)], with_payload=True)[0]
    payload = point.payload
    assert set(payload.keys()) == {
        "schema_version", "company", "kind", "title", "summary",
        "topics", "chat", "source",
    }
    for value in payload.values():
        assert not isinstance(value, bytes)
