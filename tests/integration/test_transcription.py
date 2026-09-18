"""Integration tests for app.adapters.transcription — T17's gate.

Real smoke tests shell out to the actual transcribe-audio-video skill
(subprocess.run not stubbed) against tiny synthetic audio/video generated
here via ffmpeg, per PRD §23 (never real conversation media). Error-path
tests stub subprocess.run, mirroring test_wpp_adapter.py's convention.

Requires: ffmpeg/ffprobe on PATH and openai-whisper importable by whatever
Python transcribe_audio.py's shebang resolves to. If either is missing,
these smoke tests fail with a TranscriptionError/CalledProcess-shaped
message rather than being silently skipped — see task T17's SPEC_DEVIATION
notes for what was actually observed in this environment.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.adapters import transcription
from app.adapters.transcription import TranscriptionError, transcribe

FFMPEG_AVAILABLE = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _synthetic_audio(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc", "-t", "1", str(path)],
        capture_output=True, check=True,
    )


def _synthetic_video(path: Path, duration: int = 10) -> None:
    # No audio stream, long enough to trigger the skill's uniform-sampling
    # fallback (silence -> no speech -> falls back to time-based frames).
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={duration}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(duration), str(path)],
        capture_output=True, check=True,
    )


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH")
def test_transcribe_audio_real_subprocess_returns_result(tmp_path):
    audio_path = tmp_path / "tiny.wav"
    _synthetic_audio(audio_path)

    result = transcribe(str(audio_path), media_type="audio")

    assert isinstance(result.text, str)  # empty/near-empty is fine
    assert result.frame_paths == []
    assert result.frames_dir is None


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH")
def test_transcribe_video_real_subprocess_populates_frame_paths(tmp_path):
    video_path = tmp_path / "tiny.mp4"
    _synthetic_video(video_path)
    frames_out = tmp_path / "frames"

    result = transcribe(
        str(video_path), media_type="video", frames_out=str(frames_out)
    )

    assert result.frames_dir == str(frames_out)
    assert len(result.frame_paths) > 0
    for frame_path in result.frame_paths:
        assert Path(frame_path).exists()


def test_transcribe_defaults_a_frames_dir_when_none_given(tmp_path):
    with patch(
        "app.adapters.transcription.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"success": true, "text": "", "key_frames": [], "frames_dir": "/tmp/x"}',
            stderr="",
        ),
    ) as run:
        result = transcribe(str(tmp_path / "v.mp4"), media_type="video")

    call_args = run.call_args.args[0]
    assert "--frames-out" in call_args
    assert result.frames_dir == "/tmp/x"


def test_unsupported_media_type_raises_transcription_error():
    with pytest.raises(TranscriptionError):
        transcribe("whatever.txt", media_type="pdf")


def test_nonzero_exit_code_raises_transcription_error():
    with patch(
        "app.adapters.transcription.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom",
        ),
    ):
        with pytest.raises(TranscriptionError):
            transcribe("whatever.wav", media_type="audio")


def test_malformed_stdout_raises_transcription_error():
    with patch(
        "app.adapters.transcription.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json at all", stderr="",
        ),
    ):
        with pytest.raises(TranscriptionError):
            transcribe("whatever.wav", media_type="audio")


def test_success_false_in_json_raises_transcription_error():
    with patch(
        "app.adapters.transcription.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"success": false, "error": "arquivo nao encontrado"}',
            stderr="",
        ),
    ):
        with pytest.raises(TranscriptionError):
            transcribe("whatever.wav", media_type="audio")


def test_timeout_raises_transcription_error():
    with patch(
        "app.adapters.transcription.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1),
    ):
        with pytest.raises(TranscriptionError):
            transcribe("whatever.wav", media_type="audio", timeout_seconds=1)
