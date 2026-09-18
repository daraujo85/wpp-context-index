"""Unit tests for app.domain.messages.normalize — raw shapes rooted in
app/adapters/wpp.py's history() output (id, sender, t, type, body,
optional quotedMsgId), per T2/T3's confirmed live shape.
"""
from __future__ import annotations

from app.domain.messages import MessageEnvelope, normalize

_CTX = dict(company="camila", chat_id="camila@c.us", chat_name="Camila", is_group=False)


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
