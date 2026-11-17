"""Integration test for app.services.ingestion.run — REAL local Qdrant
(same pattern as tests/integration/test_qdrant_adapter.py), wpp/inference
adapters stubbed with synthetic fixtures (never real WhatsApp data).

Sources: texto relevante (indexed), texto pessoal (guardrail-discarded),
texto misto (partially-extracted, still indexed), chat vazio (0 messages).
"""
from __future__ import annotations

import json
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
from app.services.ingestion import run

_COLLECTION = "wpp_context_test_t13"
_VECTOR_SIZE = 4  # small fake vector, no real embedding model called

@pytest.fixture
def client():
    # timeout=30: creating the text payload index on first use can exceed
    # the httpx default of 5s and flake the suite
    c = QdrantClient(url="http://127.0.0.1:6333", check_compatibility=False, timeout=30)
    if c.collection_exists(_COLLECTION):
        c.delete_collection(_COLLECTION)
    c.create_collection(
        _COLLECTION, vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE)
    )
    yield c
    c.delete_collection(_COLLECTION)
    messages = messages_collection_for(_COLLECTION)
    if c.collection_exists(messages):
        c.delete_collection(messages)

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
    # fresh tmp state path per call -> tests never see each other's
    # already-done markers (isolation, no explicit tmp_path threading needed)
    state_path = Path(tempfile.mkdtemp()) / "ingested.json"
    return SimpleNamespace(
        sources=lambda: sources, conversation_gap_minutes=20, ingestion_state_path=state_path
    )

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

def test_rerunning_same_range_skips_already_done_sources(client, monkeypatch):
    """The user's exact complaint: rerun ingest for a date range already
    processed, and sources fully done in that range (Pedro, Camila...) must
    not be re-fetched/re-classified/re-embedded — only new/incomplete ones
    should do any work."""
    history_calls = []

    def _tracked_history(target, s, e, timeout_seconds=30):
        history_calls.append(target)
        return _fake_history(target, s, e)

    monkeypatch.setattr(wpp_module, "history", _tracked_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    settings = _fake_settings(_ALL_SOURCES)
    kwargs = dict(settings=settings, collection_name=_COLLECTION, qdrant_client=client)

    run("2026-01-01T00:00:00", "2026-01-02T00:00:00", **kwargs)
    # first run fetches everyone; `vazio` is re-asked by the empty-confirm
    # rounds (an empty answer is not trusted on the first call)
    assert sorted(set(history_calls)) == sorted(s["jid"] for s in _ALL_SOURCES)

    history_calls.clear()
    summary = run("2026-01-01T00:00:00", "2026-01-02T00:00:00", **kwargs)
    assert history_calls == []  # second run, same range: nobody re-fetched
    assert summary.messages_read == 0
    assert summary.contexts_indexed == 0

    # a different range for the same sources is untouched by the state file
    history_calls.clear()
    run("2026-01-02T00:00:00", "2026-01-03T00:00:00", **kwargs)
    assert sorted(set(history_calls)) == sorted(s["jid"] for s in _ALL_SOURCES)

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

def test_on_progress_fires_start_and_done_once_per_source_in_order(client, monkeypatch):
    monkeypatch.setattr(wpp_module, "history", _fake_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    events = []
    run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings(_ALL_SOURCES),
        collection_name=_COLLECTION, qdrant_client=client,
        on_progress=lambda phase, i, t, src, s: events.append((phase, i, t, src["alias"])),
    )

    # "dead chicken" ordering: lightest sources (fewest messages) first —
    # vazio(0), pessoal(1), relevante(2), misto(2, tie broken by original order)
    expected_order = ["vazio", "pessoal", "relevante", "misto"]

    per_source = [e for e in events if e[0] in ("start", "done")]
    assert len(per_source) == 2 * len(_ALL_SOURCES)  # start+done per source
    for (phase, i, t, alias), alias_expected in zip(per_source[0::2], expected_order):
        assert phase == "start" and t == len(_ALL_SOURCES) and alias == alias_expected
    for (phase, i, t, alias), alias_expected in zip(per_source[1::2], expected_order):
        assert phase == "done" and alias == alias_expected

    # per-unit stream events also fired, source/phase only — never message content
    assert ("guardrail_discard", 2, 4, "pessoal") in events  # pessoal's unit discarded
    assert ("indexed", 3, 4, "relevante") in events
    assert ("indexed", 4, 4, "misto") in events

def test_every_prefiltered_message_lands_in_wpp_messages_linked_to_its_context(client, monkeypatch):
    """M4/T29: indexed AND guardrail-discarded units both write their
    messages; discarded ones carry context_id=None."""
    monkeypatch.setattr(wpp_module, "history", _fake_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings(_ALL_SOURCES),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    messages = messages_collection_for(_COLLECTION)
    assert summary.messages_indexed == 5
    points = {p.payload["message_id"]: p.payload for p in client.scroll(messages, limit=20)[0]}
    assert set(points) == {"true_1", "true_2", "true_3", "true_4", "true_5"}

    context_ids = {str(p.id) for p in client.scroll(_COLLECTION, limit=10)[0]}
    assert points["true_1"]["context_id"] in context_ids
    assert points["true_1"]["context_id"] == points["true_2"]["context_id"]
    assert points["true_3"]["context_id"] is None  # pessoal: discarded, still persisted
    assert points["true_1"]["text"] == "WORK_ONLY: bug 500 no checkout"
    assert points["true_1"]["day"] == "2023-11-14"
    assert points["true_1"]["media_ref"] is None

def test_history_failure_is_isolated_counted_and_left_unprocessed(client, monkeypatch):
    """Unattended 3am run: one broken chat (or a dropped session) must not
    kill the night. The failing source is retried, counted as an error, and
    left UNMARKED so the next catch-up window reprocesses exactly it."""
    calls = []

    def _history_with_failure(target, start, end, timeout_seconds=30):
        calls.append(target)
        if target == "pessoal@c.us":
            raise wpp_module.WppError("gateway: session down")
        return _fake_history(target, start, end)

    monkeypatch.setattr(wpp_module, "history", _history_with_failure)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)
    settings = _fake_settings(_ALL_SOURCES)
    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=settings, collection_name=_COLLECTION, qdrant_client=client,
    )
    assert summary.errors == 1
    assert summary.messages_read == 4          # relevante(2) + misto(2); vazio empty, pessoal failed
    assert summary.contexts_indexed >= 1       # the healthy sources still got indexed
    assert calls.count("pessoal@c.us") >= 3    # retried in every round
    done = json.loads(Path(settings.ingestion_state_path).read_text())
    assert ["pessoal", "2026-01-01T00:00:00", "2026-01-02T00:00:00"] not in done
    assert ["relevante", "2026-01-01T00:00:00", "2026-01-02T00:00:00"] in done

def test_bad_jid_is_not_retried_and_does_not_pollute_errors(client, monkeypatch):
    """chat_not_found = wrong JID in the allowlist. Permanent: one attempt,
    no error count (it's a config problem, already logged), and unmarked so
    a fixed JID gets picked up by the next window."""
    calls = []

    def _history_bad_jid(target, start, end, timeout_seconds=30):
        calls.append(target)
        if target == "nunes@c.us":
            raise wpp_module.WppChatNotFoundError("chat_not_found")
        return _fake_history(target, start, end)

    sources = _ALL_SOURCES + [{"alias": "nunes", "jid": "nunes@c.us", "is_group": False}]
    monkeypatch.setattr(wpp_module, "history", _history_bad_jid)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)
    settings = _fake_settings(sources)
    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=settings, collection_name=_COLLECTION, qdrant_client=client,
    )
    assert calls.count("nunes@c.us") == 1   # not retried in the other rounds
    assert summary.errors == 0
    done = json.loads(Path(settings.ingestion_state_path).read_text())
    assert ["nunes", "2026-01-01T00:00:00", "2026-01-02T00:00:00"] not in done

def test_empty_history_is_confirmed_before_being_trusted(client, monkeypatch):
    """Measured 2026-11-17: prata_h_levi answered [] during the 11-11 run
    (marked done) and 28 messages minutes later, same range — the gateway
    returns [] while WhatsApp Web finishes loading the chat. An empty answer
    must be re-asked, and a source that stays empty must not be marked done."""
    calls = []

    def _flaky_empty(target, start, end, timeout_seconds=30):
        calls.append(target)
        if target == "vazio@c.us":
            return []
        # "leandro" answers [] the first time, then the real messages
        if target == "relevante@c.us" and calls.count("relevante@c.us") == 1:
            return []
        return _fake_history(target, start, end)

    monkeypatch.setattr(wpp_module, "history", _flaky_empty)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)
    settings = _fake_settings(_ALL_SOURCES)
    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=settings, collection_name=_COLLECTION, qdrant_client=client,
    )
    done = json.loads(Path(settings.ingestion_state_path).read_text())
    key = ["relevante", "2026-01-01T00:00:00", "2026-01-02T00:00:00"]
    # the late-arriving source WAS recovered and processed
    assert summary.messages_read == 5   # relevante(2) + misto(2) + pessoal(1)
    assert summary.contexts_indexed >= 1
    assert key in done
    # a source that stayed empty through every confirmation round is a
    # genuinely quiet chat -> keeps its normal done marker (otherwise the
    # many permanently silent sources get re-fetched every night forever)
    assert ["vazio", "2026-01-01T00:00:00", "2026-01-02T00:00:00"] in done
    # and the late history was asked again, not accepted on the first []
    assert calls.count("relevante@c.us") >= 2

def test_failed_unit_still_persists_its_message_text(client, monkeypatch):
    """A unit whose LLM call fails used to leave NOTHING behind — no
    summary, no text. The text is the expensive part to lose (re-reading the
    chat + re-embedding), so it is written with context_id=None, like a
    guardrail-discarded unit: searchable, just not linked to a summary."""
    def _boom(unit_text):
        raise RuntimeError("9router timeout after 90s: timed out")

    monkeypatch.setattr(wpp_module, "history", _fake_history)
    monkeypatch.setattr(inference_module, "classify_and_extract", _boom)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)
    settings = _fake_settings(_ALL_SOURCES)
    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=settings, collection_name=_COLLECTION, qdrant_client=client,
    )
    assert summary.errors > 0
    assert summary.contexts_indexed == 0        # no summary was produced
    assert summary.messages_indexed > 0         # but the text was kept
    points = client.scroll(
        messages_collection_for(_COLLECTION), limit=100, with_payload=["context_id"]
    )[0]
    assert points, "messages must survive a failed summary"
    assert all(p.payload.get("context_id") is None for p in points)
