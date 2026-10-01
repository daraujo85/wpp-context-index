"""Integration test for T31: scope=messages (dense + full-text on `text`)
and expand=True on context search — real local Qdrant, stubbed embed."""
from __future__ import annotations

import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams

import app.adapters.inference as inference_module
from app.adapters.qdrant import messages_collection_for, upsert_messages
from app.services.search import search, search_messages

_COLLECTION = "wpp_context_test_t31"
_MESSAGES = messages_collection_for(_COLLECTION)
_CTX = str(uuid.uuid4())

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(inference_module, "embed", lambda text, timeout_seconds=120: [0.1, 0.2, 0.3, 0.4])
    c = QdrantClient(url="http://127.0.0.1:6333", check_compatibility=False)
    for name in (_COLLECTION, _MESSAGES):
        if c.collection_exists(name):
            c.delete_collection(name)
    c.create_collection(_COLLECTION, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    c.upsert(_COLLECTION, [PointStruct(id=_CTX, vector=[0.1, 0.2, 0.3, 0.4],
                                       payload={"title": "Deploy", "summary": "deploy quebrou", "chat": {"id": "g@g.us"}})])

    def _msg(mid, t, text, ctx):
        return {"message_id": mid, "vector": [0.4, 0.3, 0.2, 0.1], "payload": {
            "message_id": mid, "chat": {"id": "g@g.us"}, "sender": {"id": "a", "name": "Ana"},
            "timestamp": t, "type": "text", "text": text, "context_id": ctx, "media_ref": None}}
    upsert_messages([
        _msg("m2", 20, "ok, pode subir", _CTX),
        _msg("m1", 10, "erro 0xDEAD no pipeline", _CTX),
        _msg("m3", 30, "jantar hoje?", None),
    ], collection_name=_MESSAGES, client=c)
    yield c
    for name in (_COLLECTION, _MESSAGES):
        if c.collection_exists(name):
            c.delete_collection(name)

def test_messages_scope_exact_phrase_wins_and_carries_context(client):
    hits = search_messages("0xDEAD", collection_name=_COLLECTION, client=client)
    assert hits[0].message_id == "m1"
    assert hits[0].context == {"title": "Deploy", "summary": "deploy quebrou"}
    unlinked = next(h for h in hits if h.message_id == "m3")
    assert unlinked.context is None

def test_expand_attaches_messages_in_chronological_order(client):
    results = search("deploy", collection_name=_COLLECTION, client=client, expand=True)
    assert [m["message_id"] for m in results[0].messages] == ["m1", "m2"]
    assert search("deploy", collection_name=_COLLECTION, client=client)[0].messages == []
