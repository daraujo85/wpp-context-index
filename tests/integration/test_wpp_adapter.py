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

def test_history_raises_on_error_body():
    """wpp.sh exits 0 even when the gateway answers an error object. Turning
    that into "no messages" made a dead session indistinguishable from a
    quiet day — 2026-11-11 and 2026-11-13 were reported as "0 lidas · 0
    erros" while the API still had the messages."""
    body = "{\"error\":\"boom\",\"chat_not_found\":true}\nHTTP_CODE:404\n"
    with patch("app.adapters.wpp.subprocess.run", return_value=_completed(body)):
        with pytest.raises(wpp.WppError):
            wpp.history("camila", "a", "b")

# captured at import time, before conftest's autouse fixture stubs the
# module attribute — these two tests exercise the REAL implementation
_real_ensure_session = wpp.ensure_session


def test_ensure_session_returns_true_when_in_chat(monkeypatch):
    monkeypatch.setattr(wpp, "ensure_session", _real_ensure_session)
    with patch(
        "app.adapters.wpp.subprocess.run",
        return_value=_completed("{\"status\":\"inChat\"}\nHTTP_CODE:200\n"),
    ):
        assert wpp.ensure_session() is True

def test_ensure_session_returns_false_when_session_stays_down(monkeypatch):
    monkeypatch.setattr(wpp, "ensure_session", _real_ensure_session)
    """Unattended 3am run: a dead session must not abort the night, but it
    must not look like success either — the caller counts the failures."""
    with patch(
        "app.adapters.wpp.subprocess.run",
        return_value=_completed("{\"status\":\"notConnected\"}\nHTTP_CODE:200\n"),
    ):
        with patch("app.adapters.wpp.time.sleep"):
            assert wpp.ensure_session() is False

def test_history_raises_chat_not_found_distinctly():
    """A bad JID is permanent — the ingestion layer must not retry it in
    every round of every night (nunes/yuri/fernanda/fabat_ead2, 2026-11-17)."""
    body = '{"error":"Chat nao encontrado no historico.","chat_not_found":true}\nHTTP_CODE:404\n'
    with patch("app.adapters.wpp.subprocess.run", return_value=_completed(body)):
        with pytest.raises(wpp.WppChatNotFoundError):
            wpp.history("nunes", "a", "b")
