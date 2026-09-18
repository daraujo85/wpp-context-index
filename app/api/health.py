"""Liveness (/health) and readiness (/ready) routes. /health never touches a
dependency; /ready does a cheap reachability check on Qdrant (raw HTTP GET,
stdlib urllib) and the WPP adapter (`wpp.chats()`) — no heavy model load."""
from __future__ import annotations

import urllib.error
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.adapters import wpp
from app.config import Settings

router = APIRouter()


def _check_qdrant(qdrant_url: str, timeout_seconds: int = 5) -> None:
    """Raises on failure. Root path is enough to prove reachability."""
    req = urllib.request.Request(qdrant_url)
    with urllib.request.urlopen(req, timeout=timeout_seconds):
        pass


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> JSONResponse:
    settings = Settings()
    try:
        _check_qdrant(settings.qdrant_url)
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"qdrant unreachable: {exc}"},
        )

    try:
        wpp.chats()
    except wpp.WppError as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"wpp unreachable: {exc}"},
        )

    return JSONResponse(status_code=200, content={"status": "ready"})
