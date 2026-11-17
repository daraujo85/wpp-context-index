"""Integration test for app.adapters.inference — ONE real call to the local
Ollama server, per T11's gate (respects PRD's local-inference cost concerns).
Run directly: pytest tests/integration/test_inference.py -q
"""
from __future__ import annotations

import pytest

import app.adapters.inference as inference
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

def test_gateway_error_is_retried_before_failing():
    """Regression (2026-11-17): the `wpp-context` combo answered 502 on 6/6
    calls while the individual models were fine. Two attempts gave up
    immediately -> 21 of the window's 29 errors. A sick combo recovers in
    minutes, so the guardrail unit must wait it out."""
    import urllib.error
    from unittest.mock import patch

    calls = {"n": 0}

    def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(
                "http://x", 502, "Bad Gateway", {}, None
            )
        return _ok_response()

    with patch("app.adapters.inference.urllib.request.urlopen", side_effect=_flaky):
        with patch("app.adapters.inference.time.sleep"):
            text = inference._post_9router("oi", timeout_seconds=5)
    assert calls["n"] == 3
    assert text


def test_persistent_gateway_error_still_fails_fast():
    import urllib.error
    from unittest.mock import patch

    def _always_502(*args, **kwargs):
        raise urllib.error.HTTPError("http://x", 502, "Bad Gateway", {}, None)

    with patch("app.adapters.inference.urllib.request.urlopen", side_effect=_always_502):
        with patch("app.adapters.inference.time.sleep"):
            with pytest.raises(inference.InferenceError):
                inference._post_9router("oi", timeout_seconds=5)


def _ok_response():
    """Minimal context-manager response with a JSON chat.completion body."""
    import io
    import json

    body = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    }).encode()

    class _Resp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp()
