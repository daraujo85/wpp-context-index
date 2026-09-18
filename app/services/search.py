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
) -> list[SearchResult]:
    client = client or QdrantClient(url=Settings().qdrant_url)
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
    return results
