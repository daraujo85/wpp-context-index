"""Integration test for T28: wpp_messages collection helpers, real local
Qdrant, throwaway collection."""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchText

from app.adapters.qdrant import message_point_id, messages_collection_for, upsert_messages

_COLLECTION = "wpp_messages_test_t28"

@pytest.fixture
def client():
    c = QdrantClient(url="http://127.0.0.1:6333", check_compatibility=False)
    if c.collection_exists(_COLLECTION):
        c.delete_collection(_COLLECTION)
    yield c
    if c.collection_exists(_COLLECTION):
        c.delete_collection(_COLLECTION)

def _point(mid, text):
    return {"message_id": mid, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"message_id": mid, "text": text}}

def test_messages_collection_name_mapping():
    assert messages_collection_for("wpp_context") == "wpp_messages"
    assert messages_collection_for("wpp_context_test_t13") == "wpp_messages_test_t13"

def test_upsert_creates_collection_with_text_index_and_is_idempotent(client):
    upsert_messages([_point("m1", "erro no PRD-1272 checkout"), _point("m2", "ok")],
                    collection_name=_COLLECTION, client=client)
    upsert_messages([_point("m1", "erro no PRD-1272 checkout")], collection_name=_COLLECTION, client=client)

    assert client.count(_COLLECTION).count == 2
    info = client.get_collection(_COLLECTION)
    assert {"text", "context_id", "chat.id", "day"} <= set(info.payload_schema)

    hits = client.scroll(
        _COLLECTION,
        scroll_filter=Filter(must=[FieldCondition(key="text", match=MatchText(text="checkout"))]),
    )[0]
    assert [h.id for h in hits] == [message_point_id("m1")]
