"""Per-message normalized text for the `wpp_messages` layer (M4, T27).

Everything becomes text: typed text (redacted), image -> vision
description, audio -> transcript, video -> transcript + frame
descriptions, document -> filename + caption. Media binaries are never
stored — only this text plus a pointer to fetch the original.

Pure functions, no I/O.
"""
from __future__ import annotations

from app.domain.messages import MessageEnvelope, redact

_MEDIA_TAG = {"image": "[imagem]", "audio": "[audio]", "video": "[video]"}


def message_text(msg: MessageEnvelope, media_text: str | None = None) -> str:
    """Single text string for one message. `media_text` is what the media
    pipeline produced for it (description/transcript), if any."""
    parts: list[str] = []
    if msg.type == "document":
        parts.append(f"[documento: {msg.filename}]" if msg.filename else "[documento]")
    elif msg.type in _MEDIA_TAG:
        if media_text:
            parts.append(f"{_MEDIA_TAG[msg.type]} {media_text}")
        else:
            parts.append(_MEDIA_TAG[msg.type])
    if msg.body and msg.body.strip():
        parts.append(msg.body.strip())
    redacted, _ = redact("\n".join(parts))
    return redacted


# nomic-embed-text runs with a 2048-token context in Ollama; past it the
# call 500s. Token density varies (pasted logs/ids ≈ 1 token per 2 chars),
# so stay well under: ~2000 chars is safe for any text seen in practice.
EMBEDDING_MAX_CHARS = 2000


def embedding_window(
    texts: list[str], index: int, radius: int = 1, max_chars: int = EMBEDDING_MAX_CHARS
) -> str:
    """Text to embed for texts[index]: the message plus `radius` neighbors
    on each side (same unit). A bare "ok, pode subir" means nothing alone;
    its neighbors give the vector the topic. The stored `text` field stays
    only the message itself — this is purely for the vector. Capped at
    `max_chars`: if the window is too long, only the message itself
    (truncated) is embedded — its own content matters more than neighbors."""
    lo = max(0, index - radius)
    hi = min(len(texts), index + radius + 1)
    window = "\n".join(t for t in texts[lo:hi] if t)
    if len(window) <= max_chars:
        return window
    return texts[index][:max_chars]
