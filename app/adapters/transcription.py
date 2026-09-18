"""Adapter over ~/.claude/skills/transcribe-audio-video/transcribe_audio.py:
shells out via subprocess, no new mechanism (T3/T16's pattern). Script's own
shebang (`#!/usr/bin/env python3`) resolves to the system Python that has
`openai-whisper` installed — do not re-invoke it via `sys.executable`.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SKILL_SCRIPT = Path(
    "~/.claude/skills/transcribe-audio-video/transcribe_audio.py"
).expanduser()
DEFAULT_TIMEOUT_SECONDS = 300
MAX_VIDEO_FRAMES = 3  # MVP cap to keep downstream vision calls cheap.


class TranscriptionError(RuntimeError):
    """Raised when transcribe_audio.py fails or returns something we can't
    parse."""


@dataclass
class TranscriptionResult:
    text: str
    frame_paths: list[str] = field(default_factory=list)
    frames_dir: str | None = None


def transcribe(
    media_path: str,
    media_type: str,
    frames_out: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> TranscriptionResult:
    if media_type not in ("audio", "video"):
        raise TranscriptionError(f"unsupported media_type: {media_type!r}")

    args = [str(SKILL_SCRIPT), media_path, "--format", "json", "--language", "pt"]
    if media_type == "video":
        frames_out = frames_out or tempfile.mkdtemp(prefix="transcribe-frames-")
        args += ["--frames", str(MAX_VIDEO_FRAMES), "--frames-out", frames_out]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout_seconds
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TranscriptionError(f"transcribe_audio.py failed to run: {exc}") from exc

    if result.returncode != 0:
        raise TranscriptionError(
            f"transcribe_audio.py exited {result.returncode}: {result.stderr.strip()}"
        )

    # --format json prints decorative "====" banner lines around the JSON
    # blob, and (when frames were found) a human-readable "roteiro de
    # análise" after it (see transcribe_audio.py's main()) — raw_decode
    # from the first "{" and ignore whatever trails it.
    stdout = result.stdout
    try:
        start = stdout.index("{")
        data, _ = json.JSONDecoder().raw_decode(stdout, start)
    except (ValueError, json.JSONDecodeError) as exc:
        raise TranscriptionError(f"could not parse transcribe_audio.py output: {exc}") from exc

    if not data.get("success"):
        raise TranscriptionError(f"transcription failed: {data.get('error')}")

    frames = data.get("key_frames") or []
    return TranscriptionResult(
        text=data.get("text", ""),
        frame_paths=[f["frame"] for f in frames],
        frames_dir=data.get("frames_dir") if media_type == "video" else None,
    )
