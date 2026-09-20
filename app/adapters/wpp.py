"""Adapter over ~/.claude/skills/whatsapp-message/wpp.sh: shells out via
subprocess, no new mechanism. Wraps the pre-existing `chats`/`get-media`
commands plus T2's `history` command.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from app.config import Settings

WPP_SCRIPT = Path("~/.claude/skills/whatsapp-message/wpp.sh").expanduser()
DEFAULT_TIMEOUT_SECONDS = 30
_RETRY_DELAY_SECONDS = 2  # one retry on timeout only — a script exit (bad target,
# validation) is deterministic and retrying it just wastes 2s for the same result


class WppError(RuntimeError):
    """Raised when wpp.sh fails or returns something we can't parse."""


class WppMediaNotFoundError(WppError):
    """get-media resolved to no bytes (API returned {"data": null}, e.g. a
    truncated message id)."""


class WppMediaTooLargeError(WppError):
    """get-media reported a size over Settings.media_max_size_mb (PRD §21)."""


def _run(*args: str, timeout_seconds: int) -> str:
    for attempt in (1, 2):
        try:
            result = subprocess.run(
                [str(WPP_SCRIPT), *args],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise
            time.sleep(_RETRY_DELAY_SECONDS)
        except subprocess.CalledProcessError:
            # get-media in particular exits 1 on a transient non-2xx from the
            # gateway (observed under burst load) — same one retry as a
            # timeout; a real/deterministic failure (bad target, 404) just
            # fails again immediately on the retry.
            if attempt == 2:
                raise
            time.sleep(_RETRY_DELAY_SECONDS)


def _status(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> str | None:
    body = _run("status", timeout_seconds=timeout_seconds).rsplit("\nHTTP_CODE:", 1)[0]
    return json.loads(body).get("status") if body.strip() else None


def ensure_session(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
    """Self-heals a dropped WhatsApp session before a run: a down session
    doesn't raise anywhere, it just makes `history()` silently return []
    for every source (PRD run summary would show messages_read=0 across the
    board with no error). Same recovery a human does manually — bot-off
    then bot-on — via wpp.sh's existing `bot-on`/`bot-off` commands, no new
    mechanism. Gives up silently after ~20s; callers proceed either way and
    just see 0 messages read, same as today."""
    if _status(timeout_seconds) == "inChat":
        return
    _run("bot-off", timeout_seconds=timeout_seconds)
    time.sleep(3)
    _run("bot-on", timeout_seconds=timeout_seconds)
    for _ in range(9):
        time.sleep(2)
        if _status(timeout_seconds) == "inChat":
            return


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
    body = out.rsplit("\nHTTP_CODE:", 1)[0]
    return json.loads(body) if body.strip() else []


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
