"""Integration test for app.services.ingestion.run — REAL local Qdrant
(same pattern as tests/integration/test_qdrant_adapter.py), wpp/inference
adapters stubbed with synthetic fixtures (never real WhatsApp data).

Sources: texto relevante (indexed), texto pessoal (guardrail-discarded),
texto misto (partially-extracted, still indexed), chat vazio (0 messages).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

import app.adapters.inference as inference_module
import app.adapters.wpp as wpp_module
from app.domain.guardrail import GuardrailResult
from app.services.ingestion import run

_COLLECTION = "wpp_context_test_t13"
_VECTOR_SIZE = 4  # small fake vector, no real embedding model called

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

def _raw(msg_id, sender, t, body):
    return {"id": msg_id, "sender": sender, "t": t, "type": "chat", "body": body}

_HISTORY_BY_JID = {
    "relevante@c.us": [
        _raw("true_1", "a@c.us", 1_700_000_000, "WORK_ONLY: bug 500 no checkout"),
        _raw("true_2", "b@c.us", 1_700_000_010, "WORK_ONLY: confirmado, abrindo ticket"),
    ],
    "pessoal@c.us": [
        _raw("true_3", "a@c.us", 1_700_001_000, "PERSONAL_ONLY: vamos jantar hoje?"),
    ],
    "misto@g.us": [
        _raw("true_4", "a@c.us", 1_700_002_000, "MIXED_WORK_PERSONAL: deploy ok"),
        _raw("true_5", "b@c.us", 1_700_002_010, "MIXED_WORK_PERSONAL: e depois vamos ao cinema"),
    ],
    "vazio@c.us": [],
}

def _fake_history(target, start, end, timeout_seconds=30):
    return _HISTORY_BY_JID.get(target, [])

def _fake_classify_and_extract(unit_text):
    if "WORK_ONLY" in unit_text:
        return GuardrailResult(
            decision="index", work_relevance=0.9, contains_personal_content=False,
            title="Bug checkout", summary="Erro 500 no checkout", kind="bug", topics=["checkout"],
        )
    if "MIXED_WORK_PERSONAL" in unit_text:
        return GuardrailResult(
            decision="index", work_relevance=0.6, contains_personal_content=True,
            title="Deploy", summary="Deploy concluido", kind="decisao", topics=["deploy"],
        )
    return GuardrailResult(decision="discard", work_relevance=0.0, contains_personal_content=True)

def _fake_embed(text, timeout_seconds=120):
    return [0.1] * _VECTOR_SIZE

def _fake_settings(sources):
    return SimpleNamespace(sources=lambda: sources, conversation_gap_minutes=20)

_ALL_SOURCES = [
    {"alias": "relevante", "jid": "relevante@c.us", "is_group": False},
    {"alias": "pessoal", "jid": "pessoal@c.us", "is_group": False},
    {"alias": "misto", "jid": "misto@g.us", "is_group": True},
    {"alias": "vazio", "jid": "vazio@c.us", "is_group": False},
]

def test_run_aggregates_prd_metrics_and_indexes_qdrant(client, monkeypatch):
    monkeypatch.setattr(wpp_module, "history", _fake_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings(_ALL_SOURCES),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.messages_read == 5  # 2 + 1 + 2 + 0
    assert summary.prefilter_discarded == 0
    assert summary.guardrail_discarded == 1  # pessoal
    assert summary.contexts_indexed == 2  # relevante + misto
    assert summary.errors == 0
    assert summary.images_processed == 0
    assert summary.audio_processed == 0
    assert summary.videos_processed == 0
    assert summary.duration_seconds >= 0
    assert summary.run_id

    assert client.count(_COLLECTION).count == 2

def test_reingesting_same_range_does_not_duplicate_points(client, monkeypatch):
    monkeypatch.setattr(wpp_module, "history", _fake_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    kwargs = dict(
        settings=_fake_settings(_ALL_SOURCES),
        collection_name=_COLLECTION, qdrant_client=client,
    )
    run("2026-01-01T00:00:00", "2026-01-02T00:00:00", **kwargs)
    run("2026-01-01T00:00:00", "2026-01-02T00:00:00", **kwargs)

    assert client.count(_COLLECTION).count == 2

def test_source_with_no_messages_makes_no_calls_beyond_history(client, monkeypatch):
    calls = {"classify_and_extract": 0, "embed": 0}

    def _tracked_classify(unit_text):
        calls["classify_and_extract"] += 1
        return _fake_classify_and_extract(unit_text)

    def _tracked_embed(text, timeout_seconds=120):
        calls["embed"] += 1
        return _fake_embed(text)

    monkeypatch.setattr(wpp_module, "history", _fake_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _tracked_classify)
    monkeypatch.setattr(inference_module, "embed", _tracked_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings([{"alias": "vazio", "jid": "vazio@c.us", "is_group": False}]),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.messages_read == 0
    assert calls["classify_and_extract"] == 0
    assert calls["embed"] == 0
    assert client.count(_COLLECTION).count == 0

def test_unit_error_is_isolated_and_batch_continues(client, monkeypatch):
    def _raising_classify(unit_text):
        if "WORK_ONLY" in unit_text:
            raise inference_module.InferenceError("Ollama down")
        return _fake_classify_and_extract(unit_text)

    monkeypatch.setattr(wpp_module, "history", _fake_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _raising_classify)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings(_ALL_SOURCES),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.errors == 1  # relevante's unit failed classify_and_extract
    assert summary.guardrail_discarded == 1  # pessoal still processed
    assert summary.contexts_indexed == 1  # misto still processed
    assert client.count(_COLLECTION).count == 1
