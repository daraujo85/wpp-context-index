"""Builds the text string to embed for a ContextUnit (PRD §10.1): title +
summary + topics only — never raw metadata or raw message bodies.

SPEC_DEVIATION: PRD §10.1 also lists "descrição de artefatos" and "texto
técnico útil", but T11's GuardrailResult (the only extraction result
available at this point) has no `artifacts` field — it was deliberately
scoped to title/summary/kind/topics. Skipped; add artifacts text here if a
future task extends GuardrailResult with one.
"""
from __future__ import annotations

from app.domain.guardrail import GuardrailResult


def build_embedding_text(guardrail: GuardrailResult) -> str:
    parts = [guardrail.title, guardrail.summary, *guardrail.topics]
    return "\n".join(part for part in parts if part)
