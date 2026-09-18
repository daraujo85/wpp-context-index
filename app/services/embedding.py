"""Builds the text string to embed for a ContextUnit (PRD §10.1): title +
summary + topics + artifact descriptions — never raw metadata or raw
message bodies.
"""
from __future__ import annotations

from app.domain.guardrail import GuardrailResult


def build_embedding_text(guardrail: GuardrailResult) -> str:
    artifacts_text = "; ".join(a["description"] for a in guardrail.artifacts if a.get("description"))
    parts = [guardrail.title, guardrail.summary, *guardrail.topics, artifacts_text]
    return "\n".join(part for part in parts if part)
