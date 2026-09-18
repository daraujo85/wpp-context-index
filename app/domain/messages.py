"""normalize() maps a raw wpp.sh history() message dict (per PRD §6.1) to a
MessageEnvelope. Chat-level context (company/chat_id/chat_name/is_group)
isn't in the raw message — the caller passes it, since it already knows
which source it's iterating (Settings.sources()).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Raw `type` values observed from wpp.sh/WPP API -> our tracked type names.
# Unrecognized raw types map to None rather than raising.
_TYPE_MAP = {
    "chat": "text",
    "ptt": "audio",
    "audio": "audio",
    "image": "image",
    "video": "video",
    "document": "document",
}
_MEDIA_TYPES = {"image", "audio", "video", "document"}


@dataclass
class MessageEnvelope:
    company: str
    chat_id: str
    chat_name: str
    is_group: bool
    message_id: str | None
    sender_id: str | None
    sender_name: str | None
    timestamp: int | None
    type: str | None
    body: str | None
    quoted_message_id: str | None
    has_media: bool


def normalize(
    raw: dict[str, Any],
    *,
    company: str,
    chat_id: str,
    chat_name: str,
    is_group: bool,
) -> MessageEnvelope:
    msg_type = _TYPE_MAP.get(raw.get("type"))
    return MessageEnvelope(
        company=company,
        chat_id=chat_id,
        chat_name=chat_name,
        is_group=is_group,
        message_id=raw.get("id"),
        sender_id=raw.get("sender"),
        sender_name=None,  # not present in raw wpp.sh history() shape
        timestamp=raw.get("t"),
        type=msg_type,
        body=raw.get("body"),
        quoted_message_id=raw.get("quotedMsgId"),
        has_media=msg_type in _MEDIA_TYPES,
    )
