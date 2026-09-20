"""Retry behavior added for pipeline resilience: one retry on transport-level
failure (timeout/connection), never on a malformed response (that's a model
problem, not transient)."""
from __future__ import annotations

import subprocess
import urllib.error

import pytest

import app.adapters.inference as inference
import app.adapters.wpp as wpp


def test_wpp_run_retries_once_on_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(wpp, "_RETRY_DELAY_SECONDS", 0)
    calls = {"n": 0}

    def fake_run(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="wpp.sh", timeout=1)
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert wpp._run("chats", timeout_seconds=1) == "ok"
    assert calls["n"] == 2


def test_wpp_run_gives_up_after_second_timeout(monkeypatch):
    monkeypatch.setattr(wpp, "_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd="wpp.sh", timeout=1)),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        wpp._run("chats", timeout_seconds=1)


def test_inference_post_json_retries_once_on_url_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(inference, "_RETRY_DELAY_SECONDS", 0)
    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("connection refused")
        return _Resp()

    monkeypatch.setattr(inference.urllib.request, "urlopen", fake_urlopen)
    assert inference._post_json("http://x/api/generate", b"{}", 1) == {"ok": True}
    assert calls["n"] == 2


def test_inference_post_json_does_not_retry_malformed_json(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"not json"

    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(inference.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(inference.InferenceError):
        inference._post_json("http://x/api/generate", b"{}", 1)
    assert calls["n"] == 1  # no retry for a malformed response
