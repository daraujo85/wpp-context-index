"""Integration test for T21: app.services.ingestion.run() processing media
end-to-end. Mirrors test_ingestion.py's fixture pattern (T13): real local
Qdrant, wpp/inference/transcription stubbed with synthetic data.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

import app.adapters.inference as inference_module
import app.adapters.transcription as transcription_module
import app.adapters.wpp as wpp_module
from app.adapters.transcription import TranscriptionResult
from app.adapters.wpp import WppMediaNotFoundError
from app.domain.guardrail import GuardrailResult
from app.services.ingestion import run

_COLLECTION = "wpp_context_test_t21"
_VECTOR_SIZE = 4


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


def _raw(msg_id, sender, t, body, msg_type="chat"):
    return {"id": msg_id, "sender": sender, "t": t, "type": msg_type, "body": body}


@pytest.fixture
def tmp_media_dir(tmp_path):
    return tmp_path


def _fake_settings(sources):
    return SimpleNamespace(sources=lambda: sources, conversation_gap_minutes=20)


def _fake_embed(text, timeout_seconds=120):
    return [0.1] * _VECTOR_SIZE


def _fake_classify_and_extract_index(unit_text):
    return GuardrailResult(
        decision="index", work_relevance=0.9, contains_personal_content=False,
        title="Unidade com midia", summary=unit_text[:200], kind="bug", topics=["midia"],
    )


def test_image_fixture_indexes_media_and_embedding_text(client, monkeypatch, tmp_media_dir):
    image_path = tmp_media_dir / "screenshot.png"
    image_path.write_bytes(b"fake-png-bytes")

    history_by_jid = {
        "img@c.us": [_raw("img_1", "a@c.us", 1_700_000_000, None, msg_type="image")],
    }

    monkeypatch.setattr(wpp_module, "history", lambda t, s, e, timeout_seconds=30: history_by_jid.get(t, []))
    monkeypatch.setattr(wpp_module, "get_media", lambda mid, timeout_seconds=30: (str(image_path), "image/png", 14))
    monkeypatch.setattr(inference_module, "describe_image", lambda p, timeout_seconds=120: "tela de erro 500 visivel")
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract_index)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings([{"alias": "img", "jid": "img@c.us", "is_group": False}]),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.errors == 0
    assert summary.images_processed == 1
    assert summary.contexts_indexed == 1

    points = client.scroll(_COLLECTION, with_payload=True, limit=10)[0]
    assert len(points) == 1
    payload = points[0].payload
    assert payload["media"] == [{"message_id": "img_1", "type": "image", "description": "tela de erro 500 visivel"}]
    assert "tela de erro 500 visivel" in payload["summary"]
    assert not image_path.exists()  # downloaded media deleted after processing


def test_audio_fixture_embedding_text_contains_transcript(client, monkeypatch, tmp_media_dir):
    audio_path = tmp_media_dir / "voice.ogg"
    audio_path.write_bytes(b"fake-audio-bytes")

    history_by_jid = {
        "aud@c.us": [_raw("aud_1", "a@c.us", 1_700_000_000, None, msg_type="audio")],
    }

    monkeypatch.setattr(wpp_module, "history", lambda t, s, e, timeout_seconds=30: history_by_jid.get(t, []))
    monkeypatch.setattr(wpp_module, "get_media", lambda mid, timeout_seconds=30: (str(audio_path), "audio/ogg", 17))
    monkeypatch.setattr(
        transcription_module, "transcribe",
        lambda path, media_type, frames_out=None, timeout_seconds=300: TranscriptionResult(text="confirmando o pedido 123"),
    )
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract_index)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings([{"alias": "aud", "jid": "aud@c.us", "is_group": False}]),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.errors == 0
    assert summary.audio_processed == 1
    assert summary.contexts_indexed == 1

    points = client.scroll(_COLLECTION, with_payload=True, limit=10)[0]
    payload = points[0].payload
    assert "confirmando o pedido 123" in payload["summary"]
    assert payload["media"][0]["type"] == "audio"
    assert not audio_path.exists()


def test_video_fixture_embedding_text_contains_frame_description(client, monkeypatch, tmp_media_dir):
    video_path = tmp_media_dir / "clip.mp4"
    video_path.write_bytes(b"fake-video-bytes")
    frames_dir = tempfile.mkdtemp(prefix="test-frames-", dir=str(tmp_media_dir))
    frame_path = Path(frames_dir) / "frame_0.jpg"
    frame_path.write_bytes(b"fake-jpg-bytes")

    history_by_jid = {
        "vid@c.us": [_raw("vid_1", "a@c.us", 1_700_000_000, None, msg_type="video")],
    }

    monkeypatch.setattr(wpp_module, "history", lambda t, s, e, timeout_seconds=30: history_by_jid.get(t, []))
    monkeypatch.setattr(wpp_module, "get_media", lambda mid, timeout_seconds=30: (str(video_path), "video/mp4", 17))
    monkeypatch.setattr(
        transcription_module, "transcribe",
        lambda path, media_type, frames_out=None, timeout_seconds=300: TranscriptionResult(
            text="", frame_paths=[str(frame_path)], frames_dir=frames_dir,
        ),
    )
    monkeypatch.setattr(inference_module, "describe_image", lambda p, timeout_seconds=120: "tela mostrando dashboard de vendas")
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract_index)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings([{"alias": "vid", "jid": "vid@c.us", "is_group": False}]),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.errors == 0
    assert summary.videos_processed == 1
    assert summary.contexts_indexed == 1

    points = client.scroll(_COLLECTION, with_payload=True, limit=10)[0]
    payload = points[0].payload
    assert "tela mostrando dashboard de vendas" in payload["summary"]
    assert payload["media"][0]["type"] == "video"
    assert not video_path.exists()
    assert not os.path.exists(frames_dir)  # frames dir deleted after processing


def test_document_message_never_calls_media_adapters(client, monkeypatch):
    history_by_jid = {
        "doc@c.us": [_raw("doc_1", "a@c.us", 1_700_000_000, "contrato.pdf assinado", msg_type="document")],
    }
    calls = {"get_media": 0, "transcribe": 0, "describe_image": 0}

    def _tracked_get_media(mid, timeout_seconds=30):
        calls["get_media"] += 1
        raise AssertionError("get_media must not be called for document messages")

    def _tracked_transcribe(path, media_type, frames_out=None, timeout_seconds=300):
        calls["transcribe"] += 1
        raise AssertionError("transcribe must not be called for document messages")

    def _tracked_describe(path, timeout_seconds=120):
        calls["describe_image"] += 1
        raise AssertionError("describe_image must not be called for document messages")

    monkeypatch.setattr(wpp_module, "history", lambda t, s, e, timeout_seconds=30: history_by_jid.get(t, []))
    monkeypatch.setattr(wpp_module, "get_media", _tracked_get_media)
    monkeypatch.setattr(transcription_module, "transcribe", _tracked_transcribe)
    monkeypatch.setattr(inference_module, "describe_image", _tracked_describe)
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract_index)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings([{"alias": "doc", "jid": "doc@c.us", "is_group": False}]),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.errors == 0
    assert summary.contexts_indexed == 1
    assert calls == {"get_media": 0, "transcribe": 0, "describe_image": 0}

    points = client.scroll(_COLLECTION, with_payload=True, limit=10)[0]
    assert "media" not in points[0].payload
    assert "contrato.pdf assinado" in points[0].payload["summary"]


def test_media_adapter_failure_marks_unit_failed_media_and_batch_continues(client, monkeypatch, tmp_media_dir):
    ok_image_path = tmp_media_dir / "ok.png"
    ok_image_path.write_bytes(b"fake-png-bytes")

    history_by_jid = {
        "bad@c.us": [_raw("bad_1", "a@c.us", 1_700_000_000, None, msg_type="image")],
        "ok@c.us": [_raw("ok_1", "a@c.us", 1_700_001_000, None, msg_type="image")],
    }

    def _fake_get_media(message_id, timeout_seconds=30):
        if message_id == "bad_1":
            raise WppMediaNotFoundError("no media for message_id='bad_1'")
        return str(ok_image_path), "image/png", 14

    monkeypatch.setattr(wpp_module, "history", lambda t, s, e, timeout_seconds=30: history_by_jid.get(t, []))
    monkeypatch.setattr(wpp_module, "get_media", _fake_get_media)
    monkeypatch.setattr(inference_module, "describe_image", lambda p, timeout_seconds=120: "descricao ok")
    monkeypatch.setattr(inference_module, "classify_and_extract", _fake_classify_and_extract_index)
    monkeypatch.setattr(inference_module, "embed", _fake_embed)

    summary = run(
        "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        settings=_fake_settings([
            {"alias": "bad", "jid": "bad@c.us", "is_group": False},
            {"alias": "ok", "jid": "ok@c.us", "is_group": False},
        ]),
        collection_name=_COLLECTION, qdrant_client=client,
    )

    assert summary.errors == 1  # bad@c.us unit failed_media
    assert summary.contexts_indexed == 1  # ok@c.us unit still processed
    assert client.count(_COLLECTION).count == 1
    assert not ok_image_path.exists()
