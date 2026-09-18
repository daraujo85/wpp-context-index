"""Adapter over Qdrant (Settings.qdrant_url): upserts a ContextUnit +
GuardrailResult as one point, with the minimal payload from PRD §10.2.

Point ID: ContextUnit.id is a sha256 hex digest (T9's deterministic_id),
which Qdrant rejects as a point ID (it only accepts unsigned ints or UUIDs).
Verified against the installed qdrant-client (1.16.1): deriving a UUID from
the first 32 hex chars (uuid.UUID(hash[:32])) is deterministic (same
ContextUnit.id -> same UUID every time) and Qdrant accepts it — this is what
makes upsert() idempotent (same point ID overwrites, never duplicates).

SPEC_DEVIATION: PRD §10.2's payload also lists chat.name/is_group, senders,
source.first_timestamp/last_timestamp, media, urls, ticket_ids,
guardrail.version and extractor_version. None of those are available on
T9's ContextUnit or T11's GuardrailResult (both deliberately scoped
narrower than the full PRD payload), so the payload here is limited to the
fields actually available: schema_version, company, kind, title, summary,
topics, chat.id, source.message_ids.
"""
from __future__ import annotations

import uuid

from qdrant_client import QdrantClient

from app.config import Settings
from app.domain.contexts import ContextUnit
from app.domain.guardrail import GuardrailResult

SCHEMA_VERSION = 1


def point_id(unit: ContextUnit) -> str:
    return str(uuid.UUID(unit.id[:32]))


def build_payload(unit: ContextUnit, guardrail: GuardrailResult) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "company": unit.company,
        "kind": guardrail.kind,
        "title": guardrail.title,
        "summary": guardrail.summary,
        "topics": guardrail.topics,
        "chat": {"id": unit.chat_id},
        "source": {"message_ids": unit.message_ids},
    }


def upsert(
    unit: ContextUnit,
    guardrail: GuardrailResult,
    vector: list[float],
    collection_name: str = "wpp_context",
    client: QdrantClient | None = None,
) -> None:
    client = client or QdrantClient(url=Settings().qdrant_url)
    client.upsert(
        collection_name=collection_name,
        points=[{
            "id": point_id(unit),
            "vector": vector,
            "payload": build_payload(unit, guardrail),
        }],
    )
