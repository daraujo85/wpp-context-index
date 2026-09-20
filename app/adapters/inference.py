"""Inference adapter: texto (guardrail+extração, PRD §8/§9, T11) e visão via
9router (localhost:20128, OpenAI-compat, combo de assinatura sem custo por
uso — decisão revista 2026-09-19). Embedding continua no Ollama local
(Settings.ollama_base_url) — 9router não expõe endpoint de embeddings pros
combos. Combo dedicado `wpp-context` (só modelos baratos/free — deepseek-free,
gemini-flash-lite, gpt-nano, haiku — nunca fable/opus). stdlib-only HTTP
(urllib), sem dependência nova pra HTTP simples.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.config import Settings
from app.domain.guardrail import GuardrailResult, parse_guardrail_response

DEFAULT_TIMEOUT_SECONDS = 400  # generoso p/ cold-load do embedding local (Ollama); 9router responde em segundos
_RETRY_DELAY_SECONDS = 2  # one retry, transient transport errors only — not malformed responses

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


def _post_json(url: str, payload: bytes, timeout_seconds: int) -> dict:
    """POST payload, retrying once on transport-level failure (connection
    refused, timeout) after a short delay — never on a malformed/non-JSON
    response, which is a model problem a retry won't fix."""
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
                raw = resp.read()
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise InferenceError(f"Ollama call failed: {exc}") from exc
            time.sleep(_RETRY_DELAY_SECONDS)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InferenceError(f"Ollama call failed: {exc}") from exc


# -1 = nunca descarrega o modelo de embedding da VRAM entre chamadas.
# Precisa ser int ou duração com unidade ("10m") pro Go parser do Ollama —
# "-1" como string pura dá 400 "missing unit in duration".
_keep_alive_raw = os.environ.get("OLLAMA_KEEP_ALIVE", "-1")
_KEEP_ALIVE: int | str = int(_keep_alive_raw) if _keep_alive_raw.lstrip("-").isdigit() else _keep_alive_raw

# Decisão revista 2026-09-19 (substitui decisão 3/4 do plano original, "sem
# LLM externa"): texto (guardrail+extração) e visão vão pro 9router (assinatura,
# sem custo por uso — pedido explícito do Diego). Só embedding continua
# Ollama local (9router não expõe endpoint de embeddings pros combos).
_NINE_ROUTER_URL = os.environ.get("NINE_ROUTER_URL", "http://localhost:20128")
_NINE_ROUTER_COMBO = os.environ.get("NINE_ROUTER_COMBO", "wpp-context")
_NINE_ROUTER_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")


def _extract_json_object(text: str) -> str:
    """Modelos cloud às vezes envolvem o JSON em ```json ... ``` ou texto ao
    redor. Pega a maior fatia entre a 1ª '{' e a última '}'."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end != -1 else text


def _post_9router(content, timeout_seconds: int) -> str:
    """OpenAI-compat chat completions do 9router, resposta em SSE (mesmo sem
    stream:true) — junta os deltas de content num texto só. `content` é str
    (texto puro) ou lista de blocos (texto+image_url) pro caso de visão."""
    payload = json.dumps({
        "model": _NINE_ROUTER_COMBO,
        "messages": [{"role": "user", "content": content}],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{_NINE_ROUTER_URL}/v1/chat/completions", data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_NINE_ROUTER_TOKEN}",
        },
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise InferenceError(f"9router call failed: {exc}") from exc
            time.sleep(_RETRY_DELAY_SECONDS)
    text = ""
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        chunk = line[len("data: ") :].strip()
        if chunk == "[DONE]":
            break
        try:
            delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        text += delta
    return text


def classify_and_extract(
    unit_text: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> GuardrailResult:
    prompt = _PROMPT_TEMPLATE.format(unit_text=unit_text)
    response = _post_9router(prompt, timeout_seconds)
    return parse_guardrail_response(_extract_json_object(response))


def embed(text: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[float]:
    settings = Settings()
    payload = json.dumps({
        "model": settings.embedding_model,
        "prompt": text,
        "keep_alive": _KEEP_ALIVE,
    }).encode("utf-8")
    body = _post_json(f"{settings.ollama_base_url}/api/embeddings", payload, timeout_seconds)
    return body.get("embedding", [])


_MIME_BY_SUFFIX = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def describe_image(
    image_path: str | Path, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> str:
    path = Path(image_path)
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")
    content = [
        {"type": "text", "text": _VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
    ]
    description = _post_9router(content, timeout_seconds).strip()
    if not description:
        raise InferenceError("9router vision call returned empty response")
    return description
