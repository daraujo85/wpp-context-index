"""Unit tests for app.domain.messages.normalize — raw shapes rooted in
app/adapters/wpp.py's history() output (id, sender, t, type, body,
optional quotedMsgId), per T2/T3's confirmed live shape.
"""
from __future__ import annotations

from app.domain.messages import MessageEnvelope, normalize, redact, should_discard

_CTX = dict(company="camila", chat_id="camila@c.us", chat_name="Camila", is_group=False)


def _env(**overrides):
    """Builds a MessageEnvelope for should_discard tests. Defaults are a
    normal, keepable text message; override only what a case cares about.
    """
    base = dict(
        company="camila",
        chat_id="camila@c.us",
        chat_name="Camila",
        is_group=False,
        message_id="1",
        sender_id="5511@c.us",
        sender_name=None,
        timestamp=1700000000,
        type="text",
        body="oi",
        quoted_message_id=None,
        has_media=False,
    )
    base.update(overrides)
    return MessageEnvelope(**base)


def test_normalize_text_message():
    raw = {"id": "1", "sender": "5511@c.us", "t": 1700000000, "type": "chat", "body": "oi"}
    env = normalize(raw, **_CTX)
    assert env == MessageEnvelope(
        company="camila",
        chat_id="camila@c.us",
        chat_name="Camila",
        is_group=False,
        message_id="1",
        sender_id="5511@c.us",
        sender_name=None,
        timestamp=1700000000,
        type="text",
        body="oi",
        quoted_message_id=None,
        has_media=False,
    )


def test_normalize_image_message_with_quoted():
    raw = {
        "id": "2", "sender": "5511@c.us", "t": 1700000001, "type": "image",
        "body": "caption", "quotedMsgId": "1",
    }
    env = normalize(raw, **_CTX)
    assert env.type == "image"
    assert env.has_media is True
    assert env.quoted_message_id == "1"


def test_normalize_audio_message():
    raw = {"id": "3", "sender": "5511@c.us", "t": 1700000002, "type": "ptt", "body": ""}
    env = normalize(raw, **_CTX)
    assert env.type == "audio"
    assert env.has_media is True


def test_normalize_video_message():
    raw = {"id": "4", "sender": "5511@c.us", "t": 1700000003, "type": "video", "body": ""}
    env = normalize(raw, **_CTX)
    assert env.type == "video"
    assert env.has_media is True


def test_normalize_document_message():
    raw = {"id": "5", "sender": "5511@c.us", "t": 1700000004, "type": "document", "body": ""}
    env = normalize(raw, **_CTX)
    assert env.type == "document"
    assert env.has_media is True


def test_normalize_unknown_type_does_not_raise():
    raw = {"id": "6", "sender": "5511@c.us", "t": 1700000005, "type": "poll_creation", "body": None}
    env = normalize(raw, **_CTX)
    assert env.type is None
    assert env.has_media is False
    assert env.body is None


def test_normalize_group_context_passthrough():
    raw = {"id": "7", "sender": "5511@c.us", "t": 1700000006, "type": "chat", "body": "hi"}
    env = normalize(
        raw, company="times", chat_id="grp@g.us", chat_name="Time X", is_group=True
    )
    assert env.chat_id == "grp@g.us"
    assert env.is_group is True


# --- should_discard (PRD §6.2) ---------------------------------------------


def test_should_discard_keeps_normal_text_message():
    assert should_discard(_env()) is False


def test_should_discard_system_message():
    assert should_discard(_env(type="system", body=None)) is True


def test_should_discard_technical_event_without_content():
    # unrecognized raw type -> normalize() maps it to type=None; no body, no media.
    assert should_discard(_env(type=None, body=None, has_media=False)) is True


def test_should_discard_sticker_without_context():
    assert should_discard(_env(type="sticker", body=None, has_media=True)) is True


def test_should_discard_sticker_with_caption_is_kept():
    assert should_discard(_env(type="sticker", body="capivara", has_media=True)) is False


def test_should_discard_isolated_reaction():
    assert should_discard(_env(type="reaction", body="\U0001F44D")) is True


def test_should_discard_empty_message():
    assert should_discard(_env(body="", has_media=False)) is True
    assert should_discard(_env(body=None, has_media=False)) is True


def test_should_discard_duplicate_message_id():
    seen = {"1", "2"}
    assert should_discard(_env(message_id="1"), seen_ids=seen) is True
    assert should_discard(_env(message_id="3"), seen_ids=seen) is False


def test_should_discard_without_seen_ids_does_not_dedup():
    # pure function default: no external state means no dedup check.
    assert should_discard(_env(message_id="1")) is False


def test_should_discard_status_broadcast():
    assert should_discard(_env(chat_id="status@broadcast")) is True


# --- redact (PRD §6.2) ------------------------------------------------------


def test_redact_no_secret_leaves_text_unchanged():
    text = "oi, tudo bem? manda o relatorio depois"
    redacted, found = redact(text)
    assert redacted == text
    assert found is False


def test_redact_password():
    redacted, found = redact("minha senha: SuperSecreta1")
    assert found is True
    assert "SuperSecreta1" not in redacted


def test_redact_token():
    redacted, found = redact("token=abc123XYZ789")
    assert found is True
    assert "abc123XYZ789" not in redacted


def test_redact_bearer_api_key():
    redacted, found = redact("Authorization: Bearer sk-live-abcdef1234567890")
    assert found is True
    assert "sk-live-abcdef1234567890" not in redacted


def test_redact_aws_access_key():
    redacted, found = redact("chave: AKIAIOSFODNN7EXAMPLE")
    assert found is True
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted


def test_redact_otp_code():
    redacted, found = redact("seu codigo de verificacao e 482913, nao compartilhe")
    assert found is True
    assert "482913" not in redacted
