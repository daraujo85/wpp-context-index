"""Adapter over Qdrant (Settings.qdrant_url): upserts a ContextUnit +
GuardrailResult as one point, with the minimal payload from PRD §10.2.

Point ID: ContextUnit.id is a sha256 hex digest (T9's deterministic_id),
which Qdrant rejects as a point ID (it only accepts unsigned ints or UUIDs).
Verified against the installed qdrant-client (1.16.1): deriving a UUID from
the first 32 hex chars (uuid.UUID(hash[:32])) is deterministic (same
ContextUnit.id -> same UUID every time) and Qdrant accepts it — this is what
makes upsert() idempotent (same point ID overwrites, never duplicates).

SPEC_DEVIATION: PRD §10.2's payload also lists chat.name/is_group, senders,
source.first_timestamp/last_timestamp, urls, ticket_ids, guardrail.version
and extractor_version. None of those are available on T9's ContextUnit or
T11's GuardrailResult (both deliberately scoped narrower than the full PRD
payload), so the payload here is limited to the fields actually available:
schema_version, company, kind, title, summary, topics, chat.id,
source.message_ids, plus the optional media (T20) list when passed in.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from qdrant_client import QdrantClient

from app.config import Settings
from app.domain.contexts import ContextUnit
from app.domain.guardrail import GuardrailResult
from app.domain.messages import MessageEnvelope

SCHEMA_VERSION = 1


def point_id(unit: ContextUnit) -> str:
    return str(uuid.UUID(unit.id[:32]))


def build_payload(
    unit: ContextUnit,
    guardrail: GuardrailResult,
    media: list[dict] | None = None,
) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "company": unit.company,
        "kind": guardrail.kind,
        "title": guardrail.title,
        "summary": guardrail.summary,
        "topics": guardrail.topics,
        "chat": {"id": unit.chat_id},
        "source": {"message_ids": unit.message_ids},
    }
    if media:
        payload["media"] = media
    return payload


def upsert(
    unit: ContextUnit,
    guardrail: GuardrailResult,
    vector: list[float],
    collection_name: str = "wpp_context",
    client: QdrantClient | None = None,
    media: list[dict] | None = None,
) -> None:
    client = client or QdrantClient(url=Settings().qdrant_url, timeout=30)
    client.upsert(
        collection_name=collection_name,
        points=[{
            "id": point_id(unit),
            "vector": vector,
            "payload": build_payload(unit, guardrail, media=media),
        }],
    )

# ── wpp_messages (M4, T28): one point per message, always text ─────────────

MESSAGES_SCHEMA_VERSION = 1
_MESSAGE_ID_NAMESPACE = uuid.UUID("6f1d3c2e-4b7a-4e0f-9c11-7a2b5d8e9f01")

def messages_collection_for(contexts_collection: str) -> str:
    """`wpp_context` -> `wpp_messages`; a test collection `wpp_context_test_x`
    -> `wpp_messages_test_x`, so tests never touch the real messages layer."""
    if contexts_collection.startswith("wpp_context"):
        return "wpp_messages" + contexts_collection[len("wpp_context"):]
    return contexts_collection + "_messages"

def message_point_id(message_id: str) -> str:
    # message ids are opaque WhatsApp strings, not hashes -> uuid5, deterministic
    return str(uuid.uuid5(_MESSAGE_ID_NAMESPACE, message_id))

def ensure_messages_collection(client: QdrantClient, collection_name: str, vector_size: int) -> None:
    """Lazy create (nothing else in the project creates collections) + payload
    indexes: full-text on `text` for exact words/ids, keyword on the fields
    the search filters on."""
    if client.collection_exists(collection_name):
        return
    from qdrant_client.http.models import (
        Distance, PayloadSchemaType, TextIndexParams, TextIndexType, TokenizerType, VectorParams,
    )
    client.create_collection(
        collection_name, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE)
    )
    client.create_payload_index(
        collection_name, "text",
        field_schema=TextIndexParams(
            type=TextIndexType.TEXT, tokenizer=TokenizerType.MULTILINGUAL, lowercase=True,
        ),
    )
    for field_name in ("context_id", "chat.id", "day"):
        client.create_payload_index(collection_name, field_name, field_schema=PayloadSchemaType.KEYWORD)

def upsert_messages(
    points: list[dict],
    collection_name: str = "wpp_messages",
    client: QdrantClient | None = None,
) -> None:
    """points: [{"message_id", "vector", "payload"}]. Idempotent by message_id."""
    if not points:
        return
    client = client or QdrantClient(url=Settings().qdrant_url, timeout=30)
    ensure_messages_collection(client, collection_name, len(points[0]["vector"]))
    client.upsert(
        collection_name=collection_name,
        points=[
            {"id": message_point_id(p["message_id"]), "vector": p["vector"], "payload": p["payload"]}
            for p in points
        ],
    )

def build_message_payload(msg: MessageEnvelope, text: str, context_id: str | None) -> dict:
    day = (
        datetime.fromtimestamp(msg.timestamp).date().isoformat()
        if msg.timestamp is not None else None
    )
    payload = {
        "schema_version": MESSAGES_SCHEMA_VERSION,
        "message_id": msg.message_id,
        "company": msg.company,
        "chat": {"id": msg.chat_id, "is_group": msg.is_group},
        "sender": {"id": msg.sender_id, "name": msg.sender_name},
        "timestamp": msg.timestamp,
        "day": day,
        "type": msg.type,
        "text": text,
        "quoted_message_id": msg.quoted_message_id,
        "context_id": context_id,  # None = unit discarded by the guard rail
        "media_ref": None,
    }
    if msg.has_media and msg.message_id:
        payload["media_ref"] = {
            "message_id": msg.message_id,
            "fetch_url": f"/api/v1/source/media/{msg.message_id}",
        }
    return payload
