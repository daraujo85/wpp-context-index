"""Integration test for app.adapters.inference — ONE real call to the local
Ollama server, per T11's gate (respects PRD's local-inference cost concerns).
Run directly: pytest tests/integration/test_inference.py -q
"""
from __future__ import annotations

from app.adapters.inference import classify_and_extract
from app.domain.guardrail import GuardrailResult

def test_classify_and_extract_real_call_returns_valid_guardrail_result():
    unit_text = (
        "Bug: o botao de checkout nao esta fechando o modal no Safari, "
        "precisa de fix antes do deploy de sexta"
    )
    result = classify_and_extract(unit_text)
    assert isinstance(result, GuardrailResult)
    assert result.decision in ("index", "discard")
    assert isinstance(result.work_relevance, float)
    assert isinstance(result.contains_personal_content, bool)
    assert isinstance(result.redactions, list)
    assert isinstance(result.topics, list)
