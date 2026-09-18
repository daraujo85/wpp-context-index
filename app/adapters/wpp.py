"""Adapter over ~/.claude/skills/whatsapp-message/wpp.sh: shells out via
subprocess, no new mechanism. Wraps the pre-existing `chats`/`get-media`
commands plus T2's `history` command.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.config import Settings

WPP_SCRIPT = Path("~/.claude/skills/whatsapp-message/wpp.sh").expanduser()
DEFAULT_TIMEOUT_SECONDS = 30


class WppError(RuntimeError):
    """Raised when wpp.sh fails or returns something we can't parse."""


class WppMediaNotFoundError(WppError):
    """get-media resolved to no bytes (API returned {"data": null}, e.g. a
    truncated message id)."""


class WppMediaTooLargeError(WppError):
    """get-media reported a size over Settings.media_max_size_mb (PRD §21)."""


def _run(*args: str, timeout_seconds: int) -> str:
    result = subprocess.run(
        [str(WPP_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
    )
    return result.stdout


def chats(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[dict]:
    """Wraps `wpp.sh chats`. api_get appends a trailing "HTTP_CODE:<n>" line
    to stdout — strip it before parsing."""
    out = _run("chats", timeout_seconds=timeout_seconds)
    body = out.rsplit("\nHTTP_CODE:", 1)[0]
    return json.loads(body)


def history(
    target: str,
    start: str,
    end: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict]:
    """Wraps `wpp.sh history <target> --start <start> --end <end>` (start/end
    ISO 8601). Range with no messages returns []."""
    out = _run(
        "history", target, "--start", start, "--end", end,
        timeout_seconds=timeout_seconds,
    )
    return json.loads(out) if out.strip() else []


def get_media(
    message_id: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> tuple[str, str, int]:
    """Wraps `wpp.sh get-media <message_id>`. wpp.sh prints
    "<path>\\t<mime>\\t<n> bytes", not JSON — raises WppMediaNotFoundError
    when the underlying API returned {"data": null}, which wpp.sh surfaces
    as a 0-byte file (e.g. truncated message id). Raises
    WppMediaTooLargeError when the reported size exceeds
    Settings.media_max_size_mb — checks the size wpp.sh already reported,
    no re-stat() of the downloaded file."""
    out = _run("get-media", message_id, timeout_seconds=timeout_seconds)
    path, mime, size_part = out.strip().split("\t")
    size = int(size_part.split()[0])
    if size == 0:
        raise WppMediaNotFoundError(f"no media for message_id={message_id!r}")
    max_size = Settings().media_max_size_mb * 1024 * 1024
    if size > max_size:
        raise WppMediaTooLargeError(
            f"media for message_id={message_id!r} is {size} bytes, "
            f"over the {max_size} byte limit"
        )
    return path, mime, size
