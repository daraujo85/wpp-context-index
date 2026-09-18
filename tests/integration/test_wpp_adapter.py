"""Integration tests for app.adapters.wpp — subprocess.run is stubbed
(deterministic, no live wpp.sh calls), per TESTING.md's "stub subprocess"
convention for adapters.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from app.adapters import wpp


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_history_parses_json_list():
    payload = [{"id": "1", "sender": "x", "t": 1, "type": "chat", "body": "oi"}]
    with patch("app.adapters.wpp.subprocess.run", return_value=_completed(json.dumps(payload))) as run:
        result = wpp.history("camila", "2026-09-17T00:00:00Z", "2026-09-18T00:00:00Z")
    assert result == payload
    assert run.call_args.kwargs["timeout"] == wpp.DEFAULT_TIMEOUT_SECONDS


def test_history_empty_range_returns_empty_list():
    with patch("app.adapters.wpp.subprocess.run", return_value=_completed("[]\n")):
        result = wpp.history("camila", "2026-09-17T00:00:00Z", "2026-09-17T00:00:01Z")
    assert result == []


def test_get_media_returns_path_mime_size():
    with patch(
        "app.adapters.wpp.subprocess.run",
        return_value=_completed("/tmp/wpp-media-abc.jpg\timage/jpeg\t17756 bytes\n"),
    ):
        path, mime, size = wpp.get_media("true_120363...@g.us_A50E...")
    assert (path, mime, size) == ("/tmp/wpp-media-abc.jpg", "image/jpeg", 17756)


def test_get_media_raises_on_null_data():
    # wpp.sh surfaces the API's {"data": null} as a 0-byte file (truncated id case).
    with patch(
        "app.adapters.wpp.subprocess.run",
        return_value=_completed("/tmp/wpp-media-xyz.bin\tapplication/octet-stream\t0 bytes\n"),
    ):
        with pytest.raises(wpp.WppMediaNotFoundError):
            wpp.get_media("truncated-id")


def test_subprocess_timeout_is_configurable():
    with patch("app.adapters.wpp.subprocess.run", return_value=_completed("[]")) as run:
        wpp.history("camila", "a", "b", timeout_seconds=5)
    assert run.call_args.kwargs["timeout"] == 5


def test_get_media_raises_when_size_exceeds_configured_limit(monkeypatch):
    monkeypatch.setenv("MEDIA_MAX_SIZE_MB", "1")  # 1MB limit for the test
    with patch(
        "app.adapters.wpp.subprocess.run",
        return_value=_completed("/tmp/wpp-media-big.jpg\timage/jpeg\t2097152 bytes\n"),  # 2MB
    ):
        with pytest.raises(wpp.WppMediaTooLargeError):
            wpp.get_media("true_120363...@g.us_TOOBIG")


def test_get_media_within_limit_does_not_raise(monkeypatch):
    monkeypatch.setenv("MEDIA_MAX_SIZE_MB", "1")
    with patch(
        "app.adapters.wpp.subprocess.run",
        return_value=_completed("/tmp/wpp-media-ok.jpg\timage/jpeg\t1024 bytes\n"),
    ):
        path, mime, size = wpp.get_media("true_120363...@g.us_OK")
    assert (path, mime, size) == ("/tmp/wpp-media-ok.jpg", "image/jpeg", 1024)


def test_chats_strips_trailing_http_code_line():
    with patch(
        "app.adapters.wpp.subprocess.run",
        return_value=_completed('[{"id": {"_serialized": "x@c.us"}}]\nHTTP_CODE:200\n'),
    ):
        result = wpp.chats()
    assert result == [{"id": {"_serialized": "x@c.us"}}]
