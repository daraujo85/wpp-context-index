"""run(): orchestrates the end-to-end ingestion pipeline (PRD §19.1, T13).

Per allowlisted source (Settings.sources()): history -> normalize ->
should_discard/redact -> group -> classify_and_extract -> (discard | embed
+ upsert). A source with no messages in the period gets nothing beyond the
`history()` call: no normalize/group/LLM/Qdrant calls, no placeholder
write. A failing unit (LLM or embedding/Qdrant error) is counted in
`errors` and the batch moves on to the next unit — it never aborts `run()`.

Logging here is sanitized on purpose: only unit id / status / counts,
never message bodies (PRD "never log personal content/secrets").
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from app.adapters import inference, qdrant, wpp
from app.config import Settings
from app.domain.contexts import ContextUnit
from app.domain.messages import MessageEnvelope, normalize, redact, should_discard
from app.services.embedding import build_embedding_text
from app.services.grouping import group

logger = logging.getLogger(__name__)

@dataclass
class IngestionSummary:
    run_id: str
    messages_read: int = 0
    prefilter_discarded: int = 0
    guardrail_discarded: int = 0
    contexts_indexed: int = 0
    images_processed: int = 0
    audio_processed: int = 0
    videos_processed: int = 0
    errors: int = 0
    duration_seconds: float = 0.0

def _unit_text(unit: ContextUnit, messages_by_id: dict[str, MessageEnvelope]) -> str:
    bodies = []
    for message_id in unit.message_ids:
        msg = messages_by_id.get(message_id)
        if msg and msg.body:
            redacted_body, _ = redact(msg.body)
            bodies.append(redacted_body)
    return "\n".join(bodies)

def run(
    start: str,
    end: str,
    *,
    settings: Settings | None = None,
    collection_name: str = "wpp_context",
    qdrant_client=None,
) -> IngestionSummary:
    settings = settings or Settings()
    summary = IngestionSummary(run_id=uuid.uuid4().hex)
    started = time.monotonic()

    for source in settings.sources():
        raw_messages = wpp.history(source["jid"], start, end)
        summary.messages_read += len(raw_messages)
        if not raw_messages:
            continue  # zero messages in period -> no further calls, no writes

        envelopes = [
            normalize(
                raw,
                company=source["alias"],
                chat_id=source["jid"],
                chat_name=source["alias"],
                is_group=source["is_group"],
            )
            for raw in raw_messages
        ]

        seen_ids: set[str] = set()
        kept: list[MessageEnvelope] = []
        for msg in envelopes:
            if should_discard(msg, seen_ids):
                summary.prefilter_discarded += 1
            else:
                kept.append(msg)
            if msg.message_id:
                seen_ids.add(msg.message_id)
        if not kept:
            continue

        messages_by_id = {m.message_id: m for m in kept if m.message_id}
        units = group(kept, settings.conversation_gap_minutes)

        for unit in units:
            try:
                guardrail = inference.classify_and_extract(_unit_text(unit, messages_by_id))
            except Exception:
                logger.warning("unit %s status=failed_guardrail", unit.id)
                summary.errors += 1
                continue

            if guardrail.decision != "index":
                summary.guardrail_discarded += 1
                logger.info("unit %s status=skipped", unit.id)
                continue

            try:
                vector = inference.embed(build_embedding_text(guardrail))
                qdrant.upsert(
                    unit, guardrail, vector,
                    collection_name=collection_name, client=qdrant_client,
                )
            except Exception:
                logger.warning("unit %s status=failed_embedding_or_index", unit.id)
                summary.errors += 1
                continue

            summary.contexts_indexed += 1
            logger.info("unit %s status=processed", unit.id)

    summary.duration_seconds = time.monotonic() - started
    return summary
