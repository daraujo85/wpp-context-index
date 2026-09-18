"""GuardrailResult: the combined Guard Rail decision + structured extraction
(PRD §8.1 + §9), produced by ONE LLM call (T11). Fields kept to the minimal
set T11 needs: decision/work_relevance/contains_personal_content/redactions
(Guard Rail, §8.1) plus title/summary/kind/topics (extraction, §9), plus
artifacts (media descriptions, §9, added T18).

parse_guardrail_response() turns the raw (possibly malformed) LLM JSON text
into a GuardrailResult and NEVER raises — a malformed/unparseable response
falls back to a safe discard result so it can never crash the batch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

_VALID_DECISIONS = {"index", "discard"}


@dataclass
class GuardrailResult:
    decision: str
    work_relevance: float
    contains_personal_content: bool
    redactions: list[str] = field(default_factory=list)
    title: str | None = None
    summary: str | None = None
    kind: str | None = None
    topics: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)


def _discard_fallback() -> GuardrailResult:
    # Safe default on malformed input: discard, assume personal content
    # (privacy-first per PRD §2.4) rather than guessing "index".
    return GuardrailResult(
        decision="discard",
        work_relevance=0.0,
        contains_personal_content=True,
    )


def parse_guardrail_response(raw_text: str) -> GuardrailResult:
    try:
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            raise ValueError("guardrail response is not a JSON object")
        decision = data.get("decision")
        if decision not in _VALID_DECISIONS:
            raise ValueError(f"invalid decision: {decision!r}")
        return GuardrailResult(
            decision=decision,
            work_relevance=float(data.get("work_relevance", 0.0)),
            contains_personal_content=bool(data.get("contains_personal_content", False)),
            redactions=list(data.get("redactions") or []),
            title=data.get("title"),
            summary=data.get("summary"),
            kind=data.get("kind"),
            topics=list(data.get("topics") or []),
            artifacts=list(data.get("artifacts") or []),
        )
    except (json.JSONDecodeError, ValueError, TypeError):
        return _discard_fallback()
