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
    assert isinstance(result.artifacts, list)

def test_classify_and_extract_real_call_with_media_line_parses_artifacts():
    # Best-effort per T18: a local 8B model may not always populate
    # "artifacts" correctly — assert it parses without crashing, not exact content.
    unit_text = (
        "Bug reportado no grupo Operacao.\n"
        "[imagem] tela mostrando erro 500 ao salvar vencimento no painel de cobranca."
    )
    result = classify_and_extract(unit_text)
    assert isinstance(result, GuardrailResult)
    assert isinstance(result.artifacts, list)
