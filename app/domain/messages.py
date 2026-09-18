"""normalize() maps a raw wpp.sh history() message dict (per PRD §6.1) to a
MessageEnvelope. Chat-level context (company/chat_id/chat_name/is_group)
isn't in the raw message — the caller passes it, since it already knows
which source it's iterating (Settings.sources()).

should_discard()/redact() implement the deterministic pre-filter and secret
redaction from PRD §6.2 (T8).
"""
from __future__ import annotations

import re
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
    "sticker": "sticker",
    "reaction": "reaction",
    # WhatsApp system/protocol events (revoke, e2e handshake, group
    # notifications, template acks) — no user content, always discard.
    "revoked": "system",
    "e2e_notification": "system",
    "notification_template": "system",
    "gp2": "system",
    "protocol": "system",
}
_MEDIA_TYPES = {"image", "audio", "video", "document"}

# WhatsApp status/story broadcast chat id — PRD §6.2 "status".
_STATUS_CHAT_ID = "status@broadcast"


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


def should_discard(msg: MessageEnvelope, seen_ids: set[str] | None = None) -> bool:
    """Deterministic pre-filter (PRD §6.2). Discards: system messages,
    technical events without content, stickers without a caption, isolated
    reactions, empty messages, duplicates (only checked when `seen_ids` is
    given — this is a pure function, dedup state lives with the caller),
    and status/story broadcasts.

    "mensagens de chats não autorizados" (PRD §6.2) is out of scope here: a
    single MessageEnvelope carries no allowlist — that's a routing concern
    at the Settings.sources()/caller level, not decidable from the message
    itself.
    """
    if msg.chat_id == _STATUS_CHAT_ID:
        return True
    if msg.type in ("system", "reaction"):
        return True
    if msg.type == "sticker" and not (msg.body and msg.body.strip()):
        return True
    if seen_ids is not None and msg.message_id is not None and msg.message_id in seen_ids:
        return True
    body_empty = msg.body is None or msg.body.strip() == ""
    if body_empty and not msg.has_media:
        return True
    return False


# token/senha/OTP/chave de API/credenciais (PRD §6.2). Whole-match redaction
# (label included) guarantees the original secret substring never survives.
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(?:senha|password|pwd|token|api[_-]?key|chave de api|secret|credencial)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+\S+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"(?i)\b(?:c[oó]digo|otp|verification code)\b[^\d]{0,30}(\d{4,8})\b"),
]


def validate_mime(declared_type: str, mime: str) -> bool:
    """True when `mime`'s prefix (image/audio/video) matches the message's
    declared `type` (PRD §21 MIME validation). Types without a MIME family
    (document, etc.) are not checked here — always True."""
    prefix = {"image": "image/", "audio": "audio/", "video": "video/"}.get(declared_type)
    if prefix is None:
        return True
    return mime.startswith(prefix)


def redact(text: str) -> tuple[str, bool]:
    found_secrets = False

    def _mark(match: re.Match[str]) -> str:
        nonlocal found_secrets
        found_secrets = True
        return "[REDACTED]"

    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_mark, redacted)
    return redacted, found_secrets
