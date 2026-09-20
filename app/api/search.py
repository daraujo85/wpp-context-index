"""POST /api/v1/search — thin pass-through to services.search.search(),
JSON shape per PRD §11.4.

SPEC_DEVIATION: PRD §11.4 wants kind/chat_name/senders/first_timestamp/
source_message_ids/media_types/urls per result. None of those exist on
T14's SearchResult (itself scoped to what T12's Qdrant payload actually
stores — see app/adapters/qdrant.py's own SPEC_DEVIATION). Those fields
are returned as null/empty rather than invented. source_url is the one
field buildable deterministically today (no source resolver exists yet,
that's out of this task's scope).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import Settings
from app.services.search import search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    filters: dict | None = None
    limit: int = 10


@router.post("/api/v1/search")
def search_endpoint(request: SearchRequest) -> dict:
    results = search(request.query, filters=request.filters, limit=request.limit)
    settings = Settings()
    return {
        "query": request.query,
        "results": [
            {
                "id": r.context_id,
                "score": r.score,
                "title": r.title,
                "summary": r.summary,
                "kind": None,
                "chat_name": settings.chat_name(r.chat_id) if r.chat_id else None,
                "senders": [],
                "first_timestamp": None,
                "source_message_ids": [],
                "media_types": [],
                "urls": [],
                "source_url": f"/api/v1/source/{r.context_id}",
            }
            for r in results
        ],
    }
