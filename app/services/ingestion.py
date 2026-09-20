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

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.adapters import inference, qdrant, transcription, wpp
from app.config import Settings
from app.domain.contexts import ContextUnit
from app.domain.messages import (
    MessageEnvelope,
    normalize,
    redact,
    should_discard,
    validate_mime,
)
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

_ARTIFACT_TAG = {"image": "[imagem]", "audio": "[audio]", "video": "[video]"}

def _load_done(path: Path) -> set[tuple[str, str, str]]:
    """(alias, start, end) tuples already fully processed by a prior run() —
    lets a rerun over the same range skip sources it already did instead of
    re-reading/re-transcribing/re-classifying them (costly: LLM+embedding
    calls per unit). Keyed by exact range, not idempotency of the Qdrant
    write (that was already guaranteed) — this is purely to skip *work*."""
    if not path.exists():
        return set()
    return {tuple(item) for item in json.loads(path.read_text())}

def _mark_done(path: Path, key: tuple[str, str, str]) -> None:
    done = _load_done(path)
    done.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(done)))

def _unit_text(
    unit: ContextUnit,
    messages_by_id: dict[str, MessageEnvelope],
    artifacts: list[dict] | None = None,
) -> str:
    bodies = []
    for message_id in unit.message_ids:
        msg = messages_by_id.get(message_id)
        if msg and msg.body:
            redacted_body, _ = redact(msg.body)
            bodies.append(redacted_body)
    for artifact in artifacts or []:
        tag = _ARTIFACT_TAG.get(artifact.get("type"), "[midia]")
        bodies.append(f"{tag} {artifact['description']}")
    return "\n".join(bodies)

def _process_media(
    unit: ContextUnit,
    messages_by_id: dict[str, MessageEnvelope],
    summary: IngestionSummary,
) -> tuple[list[dict], list[dict]]:
    """Downloads/describes/transcribes every media message in `unit`,
    sequentially (PRD §17.2 MEDIA_CONCURRENCY=1). Returns (artifacts, media)
    for `_unit_text`/`build_payload`. Raises on any adapter error — caller
    marks the whole unit `failed_media`. `document` messages are skipped
    entirely: no download, no adapter call (PRD §6.3).
    """
    artifacts: list[dict] = []
    media: list[dict] = []
    for message_id in unit.message_ids:
        msg = messages_by_id.get(message_id)
        if not msg or not msg.has_media or msg.type == "document":
            continue

        media_path: str | None = None
        frames_dir: str | None = None
        try:
            media_path, mime, _size = wpp.get_media(message_id)
            if not validate_mime(msg.type, mime):
                raise ValueError(
                    f"message {message_id}: declared type={msg.type!r} "
                    f"does not match reported mime={mime!r}"
                )
            if msg.type == "image":
                description = inference.describe_image(media_path)
                artifacts.append({"type": "image", "description": description})
                media.append({"message_id": message_id, "type": "image", "description": description})
                summary.images_processed += 1
            elif msg.type == "audio":
                result = transcription.transcribe(media_path, media_type="audio")
                artifacts.append({"type": "audio", "description": result.text})
                media.append({"message_id": message_id, "type": "audio", "description": result.text})
                summary.audio_processed += 1
            elif msg.type == "video":
                result = transcription.transcribe(media_path, media_type="video")
                frames_dir = result.frames_dir
                for frame_path in result.frame_paths:
                    frame_description = inference.describe_image(frame_path)
                    artifacts.append({"type": "video", "description": frame_description})
                    media.append({"message_id": message_id, "type": "video", "description": frame_description})
                summary.videos_processed += 1
        finally:
            if media_path:
                try:
                    os.remove(media_path)
                except OSError:
                    pass
            if frames_dir:
                shutil.rmtree(frames_dir, ignore_errors=True)
    return artifacts, media

def run(
    start: str,
    end: str,
    *,
    settings: Settings | None = None,
    collection_name: str = "wpp_context",
    qdrant_client=None,
    on_progress=None,
) -> IngestionSummary:
    """on_progress(phase, index, total, source, summary), phase one of
    "start"|"done" (once per source) or, per unit within a source,
    "prefilter_discard"|"guardrail_start"|"guardrail_discard"|"embed_start"|
    "indexed"|"unit_error" — "*_start" fire right before the slow LLM calls
    (thinking models can take 1-2min/unit) so a UI never sits silent mid-call
    with no signal that work is actually happening. optional
    UI hook (e.g. CLI progress display / activity stream). Kept as a plain
    callback (no event class) so this module stays free of any rendering
    dependency; summary is the same mutable object throughout, so the
    callback always sees live cumulative counts. Per-unit phases never carry
    message content — only source (alias/is_group) and aggregate counts,
    per the "never log personal content" rule."""
    settings = settings or Settings()
    summary = IngestionSummary(run_id=uuid.uuid4().hex)
    started = time.monotonic()

    wpp.ensure_session()  # self-heal a dropped WhatsApp session (PRD: unattended run)

    all_sources = settings.sources()
    done_ranges = _load_done(settings.ingestion_state_path)
    pending = [s for s in all_sources if (s["alias"], start, end) not in done_ranges]
    skipped = [s for s in all_sources if (s["alias"], start, end) in done_ranges]

    # "dead chicken" ordering: fetch history for every pending source up front
    # and process the lightest ones first, so the run racks up done sources
    # fast and saves the slow/heavy ones (more units -> more LLM calls) for
    # last, instead of stalling on whichever alias happens to sort first.
    history_cache = {s["alias"]: wpp.history(s["jid"], start, end) for s in pending}
    pending.sort(key=lambda s: len(history_cache[s["alias"]]))
    sources = skipped + pending

    for index, source in enumerate(sources, start=1):
        if on_progress:
            on_progress("start", index, len(sources), source, summary)

        state_key = (source["alias"], start, end)
        if state_key in done_ranges:
            if on_progress:
                on_progress("done", index, len(sources), source, summary)
            continue  # already fully processed this exact range in a prior run()

        raw_messages = history_cache[source["alias"]]
        summary.messages_read += len(raw_messages)
        if not raw_messages:
            _mark_done(settings.ingestion_state_path, state_key)
            if on_progress:
                on_progress("done", index, len(sources), source, summary)
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
                if on_progress:
                    on_progress("prefilter_discard", index, len(sources), source, summary)
            else:
                kept.append(msg)
            if msg.message_id:
                seen_ids.add(msg.message_id)
        if not kept:
            _mark_done(settings.ingestion_state_path, state_key)
            if on_progress:
                on_progress("done", index, len(sources), source, summary)
            continue

        messages_by_id = {m.message_id: m for m in kept if m.message_id}
        units = group(kept, settings.conversation_gap_minutes)
        errors_before = summary.errors

        for unit in units:
            try:
                artifacts, media = _process_media(unit, messages_by_id, summary)
            except Exception as exc:
                logger.warning("unit %s status=failed_media error=%s", unit.id, exc)
                summary.errors += 1
                if on_progress:
                    on_progress("unit_error", index, len(sources), source, summary)
                continue

            try:
                if on_progress:
                    on_progress("guardrail_start", index, len(sources), source, summary)
                guardrail = inference.classify_and_extract(_unit_text(unit, messages_by_id, artifacts))
            except Exception as exc:
                logger.warning("unit %s status=failed_guardrail error=%s", unit.id, exc)
                summary.errors += 1
                if on_progress:
                    on_progress("unit_error", index, len(sources), source, summary)
                continue

            if guardrail.decision != "index":
                summary.guardrail_discarded += 1
                logger.info("unit %s status=skipped", unit.id)
                if on_progress:
                    on_progress("guardrail_discard", index, len(sources), source, summary)
                continue

            try:
                if on_progress:
                    on_progress("embed_start", index, len(sources), source, summary)
                vector = inference.embed(build_embedding_text(guardrail))
                qdrant.upsert(
                    unit, guardrail, vector,
                    collection_name=collection_name, client=qdrant_client,
                    media=media,
                )
            except Exception as exc:
                logger.warning("unit %s status=failed_embedding_or_index error=%s", unit.id, exc)
                summary.errors += 1
                if on_progress:
                    on_progress("unit_error", index, len(sources), source, summary)
                continue

            summary.contexts_indexed += 1
            logger.info("unit %s status=processed", unit.id)
            if on_progress:
                on_progress("indexed", index, len(sources), source, summary)

        if summary.errors == errors_before:
            _mark_done(settings.ingestion_state_path, state_key)  # only skip-worthy if fully clean
        if on_progress:
            on_progress("done", index, len(sources), source, summary)

    summary.duration_seconds = time.monotonic() - started
    return summary
