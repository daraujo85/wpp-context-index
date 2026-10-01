"""Integration test for T30: backfill_messages() over a range run() already
indexed — real local Qdrant, stubbed adapters. Proves: no text-LLM call,
indexed media reused from the summary payload (no vision call), discarded
units still written with context_id=None."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

import app.adapters.inference as inference_module
import app.adapters.wpp as wpp_module
from app.adapters.qdrant import messages_collection_for
from app.domain.guardrail import GuardrailResult
from app.services.ingestion import backfill_messages, run

_COLLECTION = "wpp_context_test_t30"
_MESSAGES = messages_collection_for(_COLLECTION)

@pytest.fixture
def client():
    c = QdrantClient(url="http://127.0.0.1:6333", check_compatibility=False)
    for name in (_COLLECTION, _MESSAGES):
        if c.collection_exists(name):
            c.delete_collection(name)
    c.create_collection(_COLLECTION, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    yield c
    for name in (_COLLECTION, _MESSAGES):
        if c.collection_exists(name):
            c.delete_collection(name)

_HISTORY = {
    "w@c.us": [
        {"id": "w1", "sender": "a", "t": 1_700_000_000, "type": "chat", "body": "WORK deploy quebrou"},
        {"id": "w2", "sender": "b", "t": 1_700_000_010, "type": "image", "caption": "print"},
    ],
    "p@c.us": [{"id": "p1", "sender": "a", "t": 1_700_000_000, "type": "chat", "body": "jantar?"}],
}

def _classify(text):
    if "WORK" in text:
        return GuardrailResult(decision="index", work_relevance=0.9, contains_personal_content=False,
                               title="Deploy", summary="deploy quebrou", kind="bug", topics=["deploy"])
    return GuardrailResult(decision="discard", work_relevance=0.0, contains_personal_content=True)

def _settings():
    return SimpleNamespace(
        sources=lambda: [
            {"alias": "w", "jid": "w@c.us", "is_group": False},
            {"alias": "p", "jid": "p@c.us", "is_group": False},
        ],
        conversation_gap_minutes=20,
        ingestion_state_path=Path(tempfile.mkdtemp()) / "ingested.json",
    )

def test_backfill_reuses_summary_and_never_calls_text_llm(client, monkeypatch, tmp_path):
    image = tmp_path / "x.png"
    monkeypatch.setattr(wpp_module, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(wpp_module, "history", lambda t, s, e, timeout_seconds=30: _HISTORY.get(t, []))
    monkeypatch.setattr(inference_module, "embed", lambda text, timeout_seconds=120: [0.1] * 4)
    monkeypatch.setattr(inference_module, "classify_and_extract", _classify)

    def _get_media(mid, timeout_seconds=30):
        image.write_bytes(b"png")
        return str(image), "image/png", 3
    monkeypatch.setattr(wpp_module, "get_media", _get_media)
    monkeypatch.setattr(inference_module, "describe_image", lambda p, timeout_seconds=120: "grafico de erro")

    run("2026-01-01T00:00:00", "2026-01-02T00:00:00", settings=_settings(),
        collection_name=_COLLECTION, qdrant_client=client)
    client.delete_collection(_MESSAGES)  # simulate: summaries exist, messages layer doesn't yet

    def _boom(*a, **k):
        raise AssertionError("backfill must not call the text LLM / vision for indexed units")
    monkeypatch.setattr(inference_module, "classify_and_extract", _boom)
    monkeypatch.setattr(inference_module, "describe_image", _boom)

    summary = backfill_messages("2026-01-01T00:00:00", "2026-01-02T00:00:00", settings=_settings(),
                                collection_name=_COLLECTION, qdrant_client=client)

    assert summary.errors == 0
    assert summary.messages_indexed == 3
    points = {p.payload["message_id"]: p.payload for p in client.scroll(_MESSAGES, limit=10)[0]}
    context_id = str(client.scroll(_COLLECTION, limit=1)[0][0].id)
    assert points["w1"]["context_id"] == context_id
    assert points["w2"]["text"] == "[imagem] grafico de erro\nprint"
    assert points["p1"]["context_id"] is None

def test_backfill_links_by_message_membership_and_skips_old_media(client, monkeypatch):
    """Summary exists but its id no longer matches the re-grouped unit (only
    shares messages) -> still linked. Unit without summary -> media never
    fetched, messages still written as tag + caption + media_ref."""
    from qdrant_client.http.models import PointStruct
    ctx = "11111111-1111-1111-1111-111111111111"
    client.upsert(_COLLECTION, [PointStruct(id=ctx, vector=[0.1] * 4, payload={
        "source": {"message_ids": ["w1", "w9"]}, "media": []})])
    monkeypatch.setattr(wpp_module, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(wpp_module, "history", lambda t, s, e, timeout_seconds=30: {
        "w@c.us": [{"id": "w1", "sender": "a", "t": 1_700_000_000, "type": "chat", "body": "deploy"}],
        "p@c.us": [{"id": "p2", "sender": "a", "t": 1_700_000_000, "type": "image", "caption": "print"}],
    }.get(t, []))
    monkeypatch.setattr(inference_module, "embed", lambda text, timeout_seconds=120: [0.1] * 4)

    def _boom(*a, **k):
        raise AssertionError("backfill must not fetch media for units without a summary")
    monkeypatch.setattr(wpp_module, "get_media", _boom)

    summary = backfill_messages("2026-01-01T00:00:00", "2026-01-02T00:00:00", settings=_settings(),
                                collection_name=_COLLECTION, qdrant_client=client)

    assert summary.errors == 0
    points = {p.payload["message_id"]: p.payload for p in client.scroll(_MESSAGES, limit=10)[0]}
    assert points["w1"]["context_id"] == ctx
    assert points["p2"]["text"] == "[imagem]\nprint"
    assert points["p2"]["media_ref"]["message_id"] == "p2"
