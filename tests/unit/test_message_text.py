"""Unit tests for app.domain.message_text (T27)."""
from __future__ import annotations

from app.domain.message_text import embedding_window, message_text
from app.domain.messages import MessageEnvelope

def _msg(**overrides):
    base = dict(
        company="c", chat_id="c@c.us", chat_name="c", is_group=False,
        message_id="m1", sender_id="s", sender_name=None, timestamp=1,
        type="text", body="bug no checkout", quoted_message_id=None,
        has_media=False,
    )
    base.update(overrides)
    return MessageEnvelope(**base)

def test_text_message_is_its_body():
    assert message_text(_msg()) == "bug no checkout"

def test_text_message_secrets_are_redacted():
    assert "abc123" not in message_text(_msg(body="senha: abc123"))

def test_image_uses_description_and_caption():
    msg = _msg(type="image", has_media=True, body="olha isso")
    assert message_text(msg, "tela de erro 500") == "[imagem] tela de erro 500\nolha isso"

def test_audio_without_transcript_keeps_tag():
    assert message_text(_msg(type="audio", has_media=True, body=None)) == "[audio]"

def test_document_uses_filename():
    msg = _msg(type="document", has_media=True, body="segue", filename="rel.pdf")
    assert message_text(msg) == "[documento: rel.pdf]\nsegue"

def test_embedding_window_includes_neighbors_only():
    texts = ["a", "b", "c", "d"]
    assert embedding_window(texts, 0) == "a\nb"
    assert embedding_window(texts, 2) == "b\nc\nd"
    assert embedding_window(texts, 3, radius=0) == "d"

def test_embedding_window_too_long_falls_back_to_own_message_truncated():
    texts = ["a" * 1500, "b" * 3000, "c"]
    assert embedding_window(texts, 1, max_chars=2000) == "b" * 2000
    assert embedding_window(texts, 2, max_chars=2000) == "c"  # neighbor "b"*3000 pushes it over
