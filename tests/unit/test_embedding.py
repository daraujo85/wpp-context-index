"""Unit tests for app.services.embedding.build_embedding_text — pure string
building, no network. Confirms title+summary+topics only, never raw
metadata/message bodies."""
from __future__ import annotations

from app.domain.guardrail import GuardrailResult
from app.services.embedding import build_embedding_text


def _guardrail(**overrides) -> GuardrailResult:
    base = dict(
        decision="index",
        work_relevance=0.9,
        contains_personal_content=False,
        title="Erro 500 ao alterar vencimento",
        summary="No grupo Operação foi reportado erro 500.",
        topics=["vencimento", "cobranca"],
    )
    base.update(overrides)
    return GuardrailResult(**base)


def test_embedding_text_contains_title_summary_and_topics():
    text = build_embedding_text(_guardrail())
    assert "Erro 500 ao alterar vencimento" in text
    assert "No grupo Operação foi reportado erro 500." in text
    assert "vencimento" in text
    assert "cobranca" in text


def test_embedding_text_omits_none_fields():
    guardrail = _guardrail(title=None, summary=None, topics=[])
    text = build_embedding_text(guardrail)
    assert text == ""


def test_embedding_text_never_includes_decision_or_relevance_fields():
    # Regression: embedding text must never leak raw metadata fields.
    text = build_embedding_text(_guardrail())
    assert "index" not in text
    assert "0.9" not in text


def test_embedding_text_includes_artifact_descriptions():
    guardrail = _guardrail(
        artifacts=[
            {"type": "image", "description": "print de erro 500 no app"},
            {"type": "audio", "description": "áudio confirmando vencimento"},
        ]
    )
    text = build_embedding_text(guardrail)
    assert "print de erro 500 no app" in text
    assert "áudio confirmando vencimento" in text


def test_embedding_text_empty_artifacts_matches_previous_output():
    text_with_empty = build_embedding_text(_guardrail(artifacts=[]))
    text_without_field = build_embedding_text(_guardrail())
    assert text_with_empty == text_without_field
