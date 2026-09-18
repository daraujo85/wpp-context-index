"""GET /api/v1/source/{context_id} and GET /api/v1/source/media/{message_id}
(T22). Reads the Qdrant point by context_id (same UUID T14's SearchResult.
context_id already returns) via the existing qdrant.py client pattern, and
delegates media fetch straight to wpp.get_media (T3) with the temp file
deleted after the response is sent (Starlette BackgroundTask).

SPEC_DEVIATION (carried over from T12/T14): the payload doesn't store
per-message sender/timestamp yet, so those fields from PRD §12's example
JSON are omitted here too, not fabricated -- this endpoint returns exactly
what's actually indexed.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from qdrant_client import QdrantClient
from starlette.background import BackgroundTask

from app.adapters import wpp
from app.config import Settings

router = APIRouter()


@router.get("/api/v1/source/{context_id}")
def get_source(context_id: str) -> dict:
    client = QdrantClient(url=Settings().qdrant_url)
    try:
        points = client.retrieve("wpp_context", ids=[context_id], with_payload=True)
    except Exception:  # noqa: BLE001 - malformed id (not a UUID) -> 404, not 500
        points = []
    if not points:
        raise HTTPException(status_code=404, detail="context_id not found")

    payload = points[0].payload or {}
    return {
        "chat": {"id": (payload.get("chat") or {}).get("id")},
        "messages": [
            {"message_id": mid} for mid in (payload.get("source") or {}).get("message_ids", [])
        ],
        "media": [
            {
                "message_id": item["message_id"],
                "fetch_url": f"/api/v1/source/media/{item['message_id']}",
            }
            for item in payload.get("media") or []
        ],
    }


@router.get("/api/v1/source/media/{message_id}")
def get_source_media(message_id: str) -> FileResponse:
    try:
        path, mime, _size = wpp.get_media(message_id)
    except wpp.WppMediaNotFoundError:
        raise HTTPException(status_code=404, detail="media not found") from None
    return FileResponse(path, media_type=mime, background=BackgroundTask(os.remove, path))
