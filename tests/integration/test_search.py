"""Integration test for app.services.search.search — REAL local Qdrant +
REAL local embedding model (mxbai-embed-large via Ollama, same as
T12/T13), no mocking of inference.embed here (paraphrase matching needs
real embeddings to mean anything). No generative LLM call anywhere.

Uses a distinctly-named test-only collection (separate from "wpp_context"
and from T12/T13's own test collections) and deletes it at the end.
"""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from app.adapters import inference
from app.adapters.qdrant import upsert
from app.domain.contexts import ContextUnit, deterministic_id
from app.domain.guardrail import GuardrailResult
from app.services.search import search

_COLLECTION = "wpp_context_test_t14"
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


def _unit(chat_id, message_ids):
    return ContextUnit(
        id=deterministic_id("acme", chat_id, message_ids, "v1"),
        company="acme",
        chat_id=chat_id,
        message_ids=message_ids,
    )


def _index(client, chat_id, message_ids, title, summary, topics):
    unit = _unit(chat_id, message_ids)
    guardrail = GuardrailResult(
        decision="index", work_relevance=0.9, contains_personal_content=False,
        title=title, summary=summary, kind="bug", topics=topics,
    )
    text_to_embed = "\n".join([title, summary, *topics])
    vector = inference.embed(text_to_embed)
    upsert(unit, guardrail, vector, collection_name=_COLLECTION, client=client)


def test_search_by_paraphrase_finds_unit_indexed_with_different_words(client):
    _index(
        client, "checkout@g.us", ["m1", "m2"],
        title="Falha no pagamento do carrinho",
        summary="Cliente reporta que o pagamento nao finaliza no checkout.",
        topics=["pagamento", "checkout"],
    )
    _index(
        client, "rh@g.us", ["m3"],
        title="Ferias aprovadas",
        summary="Solicitacao de ferias do time aprovada pelo gestor.",
        topics=["rh", "ferias"],
    )

    results = search(
        "problema pra concluir a compra no site",
        collection_name=_COLLECTION, client=client,
    )

    assert results[0].chat_id == "checkout@g.us"


def test_search_by_exact_ticket_id_boosts_over_semantically_close_result(client):
    _index(
        client, "checkout@g.us", ["m1"],
        title="Bug ABC-123 no checkout",
        summary="Erro 500 ao finalizar compra, ticket ABC-123 aberto.",
        topics=["checkout", "bug"],
    )
    # semantically very close (same topic/wording) but different ticket
    _index(
        client, "checkout-outro@g.us", ["m2"],
        title="Bug DEF-999 no checkout",
        summary="Erro 500 ao finalizar compra, ticket DEF-999 aberto.",
        topics=["checkout", "bug"],
    )

    results = search(
        "status do ticket ABC-123", collection_name=_COLLECTION, client=client,
    )

    assert results[0].chat_id == "checkout@g.us"
