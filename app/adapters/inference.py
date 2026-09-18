"""Adapter over the local Ollama server (Settings.ollama_base_url): one HTTP
call combining the Guard Rail decision + structured extraction (PRD §8/§9,
T11) into a single JSON-mode generate request. stdlib-only HTTP (urllib),
consistent with app/config.py's dependency-minimalism — no new dependency
for a single POST.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from app.config import Settings
from app.domain.guardrail import GuardrailResult, parse_guardrail_response

DEFAULT_TIMEOUT_SECONDS = 120

_VISION_PROMPT = """Descreva esta imagem de forma objetiva, para indexação e busca
em um índice corporativo. Não faça descrição artística. Foque em:
- Texto visível (transcreva literalmente)
- Mensagens de erro, se houver
- Tela ou sistema mostrado (nome do app, tipo de tela)
- Botões/ações destacados

Responda em texto corrido, direto."""

_PROMPT_TEMPLATE = """Você avalia mensagens de trabalho do WhatsApp para um índice corporativo.

Tarefa (uma única resposta JSON, sem texto fora do JSON):
1. Guard Rail: decida se a unidade de conversa abaixo tem relevância de trabalho
   e não é apenas conteúdo pessoal. Se misturar pessoal e profissional, extraia
   e resuma somente a parte profissional.
2. Se "decision" for "index", extraia também um resumo estruturado.

Responda apenas com um objeto JSON com exatamente estas chaves:
{{
  "decision": "index" ou "discard",
  "work_relevance": número entre 0 e 1,
  "contains_personal_content": true ou false,
  "redactions": lista de strings (segredos/PII removidos, se houver),
  "title": título curto (string, ou null se decision="discard"),
  "summary": resumo objetivo (string, ou null se decision="discard"),
  "kind": categoria curta como "bug", "decisao", "artifact" (ou null),
  "topics": lista de palavras-chave (pode ser vazia),
  "artifacts": lista de objetos {{"type": "image"|"audio"|"video", "description": "..."}}
    — preencha somente se a unidade de conversa abaixo contiver uma descrição de
    imagem/print ou uma transcrição de áudio/vídeo (ex.: linhas marcadas como
    "[imagem]", "[audio]", "[video]"); senão deixe []
}}

Unidade de conversa:
\"\"\"{unit_text}\"\"\"
"""


class InferenceError(RuntimeError):
    """Raised when the Ollama HTTP call itself fails (network/timeout/status)."""


def classify_and_extract(
    unit_text: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> GuardrailResult:
    settings = Settings()
    payload = json.dumps({
        "model": settings.text_model,
        "prompt": _PROMPT_TEMPLATE.format(unit_text=unit_text),
        "format": "json",
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.ollama_base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise InferenceError(f"Ollama call failed: {exc}") from exc
    return parse_guardrail_response(body.get("response", ""))


def embed(text: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[float]:
    settings = Settings()
    payload = json.dumps({
        "model": settings.embedding_model,
        "prompt": text,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.ollama_base_url}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise InferenceError(f"Ollama call failed: {exc}") from exc
    return body.get("embedding", [])


def describe_image(
    image_path: str | Path, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> str:
    settings = Settings()
    image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    payload = json.dumps({
        "model": settings.vision_model,
        "prompt": _VISION_PROMPT,
        "images": [image_b64],
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.ollama_base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise InferenceError(f"Ollama call failed: {exc}") from exc
    description = body.get("response", "").strip()
    if not description:
        raise InferenceError("Ollama vision call returned empty response")
    return description
