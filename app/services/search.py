"""Busca (T14): embedding da query -> Qdrant dense search -> boost lexical
quando a query bate um padrão de identificador exato (URL/ticket/hash).

Sem LLM generativa aqui: só `inference.embed()` (retrieval, não geração) +
leitura no Qdrant + regex/string local.

SPEC_DEVIATION: a descrição da task pede boost em topics/title/urls, mas o
payload atual (app/adapters/qdrant.py build_payload, T12 SPEC_DEVIATION) não
tem `urls`/`ticket_ids` — só `title`, `summary`, `topics`. Boost aplicado
contra esses três; sem inventar campos que T12/PRD ainda não escreveram.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from qdrant_client import QdrantClient

from app.adapters import inference
from app.adapters.qdrant import messages_collection_for
from app.config import Settings

# Identificador "exato": ticket-like (ABC-123), hash hex (>=7 chars), URL.
_IDENTIFIER_PATTERN = re.compile(
    r"[A-Z]{2,}-\d+|https?://\S+|\b[0-9a-f]{7,40}\b", re.IGNORECASE
)

_LEXICAL_BOOST = 1000.0  # so um match exato supera qualquer score semantico (0..1)


@dataclass
class SearchResult:
    context_id: str
    score: float
    title: str | None
    summary: str | None
    chat_id: str | None
    topics: list[str] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)  # only filled with expand=True

@dataclass
class MessageHit:
    message_id: str
    score: float
    text: str | None
    chat_id: str | None
    sender: dict
    timestamp: int | None
    type: str | None
    context_id: str | None
    media_ref: dict | None
    context: dict | None = None  # {title, summary} of the summary it belongs to


def _lexical_fields(payload: dict) -> list[str]:
    chat = payload.get("chat") or {}
    return [
        payload.get("title") or "",
        payload.get("summary") or "",
        *(payload.get("topics") or []),
    ]


def search(
    query: str,
    filters: dict | None = None,
    limit: int = 10,
    collection_name: str = "wpp_context",
    client: QdrantClient | None = None,
    expand: bool = False,
) -> list[SearchResult]:
    client = client or QdrantClient(url=Settings().qdrant_url, timeout=30)
    vector = inference.embed(query)

    query_filter = None
    if filters and "chat_id" in filters:
        query_filter = {"must": [{"key": "chat.id", "match": {"value": filters["chat_id"]}}]}

    response = client.query_points(
        collection_name=collection_name,
        query=vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )

    identifier_match = _IDENTIFIER_PATTERN.search(query)
    identifier = identifier_match.group(0) if identifier_match else None

    results = []
    for point in response.points:
        payload = point.payload or {}
        score = float(point.score)
        if identifier and any(identifier in field_ for field_ in _lexical_fields(payload)):
            score += _LEXICAL_BOOST
        results.append(SearchResult(
            context_id=str(point.id),
            score=score,
            title=payload.get("title"),
            summary=payload.get("summary"),
            chat_id=(payload.get("chat") or {}).get("id"),
            topics=payload.get("topics") or [],
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    if expand:
        messages_collection = messages_collection_for(collection_name)
        for r in results:
            r.messages = context_messages(r.context_id, messages_collection, client)
    return results

def _message_fields(payload: dict) -> dict:
    return {
        "message_id": payload.get("message_id"),
        "text": payload.get("text"),
        "sender": payload.get("sender") or {},
        "timestamp": payload.get("timestamp"),
        "type": payload.get("type"),
        "media_ref": payload.get("media_ref"),
    }

def context_messages(context_id: str, collection_name: str, client: QdrantClient) -> list[dict]:
    """All wpp_messages of one summary, chronological."""
    if not client.collection_exists(collection_name):
        return []
    points, _ = client.scroll(
        collection_name,
        scroll_filter={"must": [{"key": "context_id", "match": {"value": context_id}}]},
        limit=1000,
        with_payload=True,
    )
    messages = [_message_fields(p.payload or {}) for p in points]
    return sorted(messages, key=lambda m: m["timestamp"] or 0)

def search_messages(
    query: str,
    filters: dict | None = None,
    limit: int = 10,
    collection_name: str = "wpp_context",
    client: QdrantClient | None = None,
) -> list[MessageHit]:
    """scope=messages: dense search over wpp_messages, plus a full-text
    pass on `text` — every query token present in the message gives the
    lexical boost (exact phrase/number/link said in the chat wins). Each
    hit carries the title/summary of its context, when it has one."""
    client = client or QdrantClient(url=Settings().qdrant_url, timeout=30)
    messages_collection = messages_collection_for(collection_name)
    if not client.collection_exists(messages_collection):
        return []

    must = []
    if filters and "chat_id" in filters:
        must.append({"key": "chat.id", "match": {"value": filters["chat_id"]}})

    response = client.query_points(
        collection_name=messages_collection,
        query=inference.embed(query),
        query_filter={"must": must} if must else None,
        limit=limit,
        with_payload=True,
    )
    lexical, _ = client.scroll(
        messages_collection,
        scroll_filter={"must": [*must, {"key": "text", "match": {"text": query}}]},
        limit=limit,
        with_payload=True,
    )

    by_id: dict[str, tuple[float, dict]] = {
        str(p.id): (float(p.score), p.payload or {}) for p in response.points
    }
    for p in lexical:
        score, payload = by_id.get(str(p.id), (0.0, p.payload or {}))
        by_id[str(p.id)] = (score + _LEXICAL_BOOST, payload)

    hits = [
        MessageHit(
            score=score,
            chat_id=(payload.get("chat") or {}).get("id"),
            context_id=payload.get("context_id"),
            **_message_fields(payload),
        )
        for score, payload in by_id.values()
    ]
    hits.sort(key=lambda h: h.score, reverse=True)
    hits = hits[:limit]

    context_ids = list({h.context_id for h in hits if h.context_id})
    if context_ids:
        contexts = {
            str(p.id): p.payload or {}
            for p in client.retrieve(collection_name, ids=context_ids, with_payload=True)
        }
        for h in hits:
            ctx = contexts.get(h.context_id or "")
            if ctx:
                h.context = {"title": ctx.get("title"), "summary": ctx.get("summary")}
    return hits
