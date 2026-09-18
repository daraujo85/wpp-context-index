"""Unit tests for app.domain.guardrail.parse_guardrail_response — hand-written
JSON strings, no network call. Scenarios mirror PRD §8.1's two examples."""
from __future__ import annotations

import json

from app.domain.guardrail import GuardrailResult, parse_guardrail_response

def test_prd_8_1_example_1_work_relevant_indexes():
    raw = json.dumps({
        "decision": "index",
        "work_relevance": 0.94,
        "contains_personal_content": False,
        "categories": ["bug", "operacao"],
        "reason": "Discussão de erro operacional reproduzido em tela.",
        "redactions": [],
        "title": "Erro 500 ao alterar vencimento",
        "summary": "No grupo Operação foi reportado erro 500 ao salvar nova data de vencimento.",
        "kind": "bug",
        "topics": ["vencimento", "cobranca"],
    })
    result = parse_guardrail_response(raw)
    assert result == GuardrailResult(
        decision="index",
        work_relevance=0.94,
        contains_personal_content=False,
        redactions=[],
        title="Erro 500 ao alterar vencimento",
        summary="No grupo Operação foi reportado erro 500 ao salvar nova data de vencimento.",
        kind="bug",
        topics=["vencimento", "cobranca"],
    )

def test_prd_8_1_example_2_personal_content_discards():
    raw = json.dumps({
        "decision": "discard",
        "work_relevance": 0.08,
        "contains_personal_content": True,
        "categories": ["personal"],
        "reason": "Conversa pessoal sem relação com trabalho.",
        "redactions": [],
    })
    result = parse_guardrail_response(raw)
    assert result.decision == "discard"
    assert result.contains_personal_content is True
    assert result.work_relevance == 0.08

def test_malformed_json_falls_back_to_discard():
    result = parse_guardrail_response("not json at all {{{")
    assert result.decision == "discard"

def test_missing_decision_field_falls_back_to_discard():
    raw = json.dumps({"work_relevance": 0.5, "contains_personal_content": False})
    result = parse_guardrail_response(raw)
    assert result.decision == "discard"

def test_invalid_decision_value_falls_back_to_discard():
    raw = json.dumps({"decision": "maybe", "work_relevance": 0.5})
    result = parse_guardrail_response(raw)
    assert result.decision == "discard"

def test_non_object_json_falls_back_to_discard():
    result = parse_guardrail_response("[1, 2, 3]")
    assert result.decision == "discard"

def test_fallback_never_shares_mutable_state_between_calls():
    a = parse_guardrail_response("garbage")
    b = parse_guardrail_response("garbage")
    a.redactions.append("x")
    assert b.redactions == []
