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

import logging
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import Settings
from app.services.search import search, search_messages

router = APIRouter()

# Auditoria de uso: o uvicorn só loga método+path+status, então é impossível
# saber QUEM buscou O QUÊ — nem distinguir uso de agente de teste manual.
# Uma linha por busca, greppável: `docker logs <container> | grep search-audit`.
audit_logger = logging.getLogger("wpp_context.search_audit")
if not audit_logger.handlers:
    # uvicorn configura só os loggers "uvicorn*" — o root fica em WARNING, então
    # um .info() nosso era descartado antes de chegar em qualquer handler.
    # Handler próprio + propagate=False: sai limpo no docker logs, nível INFO.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    audit_logger.addHandler(_handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False


class SearchRequest(BaseModel):
    query: str
    filters: dict | None = None
    limit: int = 10
    scope: Literal["contexts", "messages"] = "contexts"
    expand: bool = False  # contexts scope: attach each summary's original messages


def _message_hit(h, settings: Settings) -> dict:
    return {
        "message_id": h.message_id,
        "score": h.score,
        "text": h.text,
        "chat_name": settings.chat_name(h.chat_id) if h.chat_id else None,
        "sender": h.sender,
        "timestamp": h.timestamp,
        "type": h.type,
        "media_ref": h.media_ref,
        "context_id": h.context_id,
        "context": h.context,
        "source_url": f"/api/v1/source/{h.context_id}" if h.context_id else None,
    }


@router.post("/api/v1/search")
def search_endpoint(request: SearchRequest, http: Request) -> dict:
    started = time.monotonic()
    settings = Settings()
    if request.scope == "messages":
        hits = search_messages(request.query, filters=request.filters, limit=request.limit)
        response = {
            "query": request.query,
            "scope": "messages",
            "results": [_message_hit(h, settings) for h in hits],
        }
    else:
        # expand only passed when set, so the default call stays search(q, filters, limit)
        extra = {"expand": True} if request.expand else {}
        results = search(request.query, filters=request.filters, limit=request.limit, **extra)
        response = {
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
                    **({"messages": r.messages} if request.expand else {}),
                }
                for r in results
            ],
        }

    audit_logger.info(
        "search-audit client=%s scope=%s limit=%s results=%d ms=%.0f query=%r filters=%s",
        http.client.host if http.client else "-",
        request.scope,
        request.limit,
        len(response.get("results", [])),
        (time.monotonic() - started) * 1000,
        request.query,
        request.filters,
    )
    return response
