"""ContextUnit: a grouped conversation unit (PRD §9/§10). Minimal shape for
now — title/summary/topics/etc are T11/T12's job to add when they extend
this dataclass.

deterministic_id() gives the idempotent id required by PRD's dedup/upsert
flow: same (company, chat_id, message_ids, extractor_version) must always
produce the same id, regardless of message_ids order.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class ContextUnit:
    id: str
    company: str
    chat_id: str
    message_ids: list[str]


def deterministic_id(
    company: str, chat_id: str, message_ids: list[str], extractor_version: str
) -> str:
    normalized = "|".join([company, chat_id, ",".join(sorted(message_ids)), extractor_version])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
