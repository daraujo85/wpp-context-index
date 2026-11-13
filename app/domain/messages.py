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
    # `album` is only a container: its images arrive as their own `image`
    # messages. `ciphertext` = not yet decrypted, no readable content.
    "album": "system",
    "ciphertext": "system",
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
    filename: str | None = None


def _sender(raw: dict[str, Any], is_group: bool) -> tuple[str | None, str | None]:
    """In groups the real author is `author` (@lid); `sender` comes as a
    contact dict (id/pushname/name), or a plain string in older fixtures.

    2026-11-13: wpp.sh's `history` now enxuga a resposta antes de entregar
    (HIST_LEAN_JQ, wpp.sh) e já separa `sender_id`/`sender_name` no próprio
    JSON — não manda mais o dict `sender` cru (que carregava ~20 campos
    irrelevantes por contato: profilePicThumbObj, labels, isWAContact...).
    Esse formato novo tem prioridade; o fallback pro `sender` dict/string
    cru fica só por segurança, caso algo chame normalize() com payload
    não-enxuto (ex: teste unitário com fixture antiga)."""
    if "sender_id" in raw or "sender_name" in raw:
        sender_id = raw.get("sender_id")
        sender_name = raw.get("sender_name")
    else:
        sender = raw.get("sender")
        sender_id = sender.get("id", {}).get("_serialized") if isinstance(sender, dict) else sender
        sender_name = None
        if isinstance(sender, dict):
            sender_name = sender.get("pushname") or sender.get("name") or sender.get("formattedName")
    if is_group and raw.get("author"):
        sender_id = raw["author"]
    return sender_id, sender_name


_THUMBNAIL = re.compile(r"^[A-Za-z0-9+/=\s]{64,}$")  # base64 blob, no real words — fallback pra payload não-enxuto


def _body(raw: dict[str, Any], msg_type: str | None) -> str | None:
    """For media, `body` is empty/null or a tiny base64 thumbnail — the text
    the user actually typed is in `caption`.

    2026-11-13: wpp.sh's `history` já zera `body` (null) pra mídia antes de
    entregar (HIST_LEAN_JQ) — o regex `_THUMBNAIL` nunca mais deveria casar
    de verdade, mas fica como rede de segurança caso o payload não passe
    pelo enxugamento (ex: fixture antiga, chamada direta da API crua)."""
    body = raw.get("body")
    if msg_type in _MEDIA_TYPES:
        if raw.get("caption"):
            return raw["caption"]
        return None if not body or _THUMBNAIL.match(body) else body
    return body


def normalize(
    raw: dict[str, Any],
    *,
    company: str,
    chat_id: str,
    chat_name: str,
    is_group: bool,
) -> MessageEnvelope:
    msg_type = _TYPE_MAP.get(raw.get("type"))
    sender_id, sender_name = _sender(raw, is_group)
    timestamp = raw.get("t")
    return MessageEnvelope(
        company=company,
        chat_id=chat_id,
        chat_name=chat_name,
        is_group=is_group,
        message_id=raw.get("id"),
        sender_id=sender_id,
        sender_name=sender_name,
        timestamp=int(timestamp) if timestamp is not None else None,  # gateway sends "t" as str
        type=msg_type,
        body=_body(raw, msg_type),
        quoted_message_id=raw.get("quotedMsgId"),
        has_media=msg_type in _MEDIA_TYPES,
        filename=raw.get("filename"),
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
