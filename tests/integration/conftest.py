"""Shared integration-test fixtures.

The suite must never touch the live WhatsApp session or sleep through retry
backoffs: `run()` self-heals the session before fetching (`ensure_session()`)
and retries failed fetches with a 10s backoff. Both are stubbed here once —
before the per-test modules had to remember to do it, and the ones that
forgot started shelling out to the real `wpp.sh` (120s timeout each), which
turned the suite into a multi-minute hang.
"""
from __future__ import annotations

import pytest

import app.adapters.wpp as wpp_module


@pytest.fixture(autouse=True)
def _no_live_whatsapp_session(monkeypatch):
    monkeypatch.setattr(wpp_module, "ensure_session", lambda *a, **k: True)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
