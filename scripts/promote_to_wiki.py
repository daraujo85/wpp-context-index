#!/usr/bin/env python3
"""Ponte wpp-context-index -> Wiki (kb-repo/docs).

Varre contextos recém-indexados com kind="decisao" no Qdrant (wpp_context),
passa cada um por um LLM-judge conservador ("isso merece virar referência
permanente na Wiki, ou é só chat efêmero de trabalho?"), e só os aprovados
viram um .md em kb-repo/docs/decisoes-wpp/ + entrada no índice
decisoes-wpp.md — seguindo o mesmo padrão de front-matter de dailies.md/
retrospectivas.md.

NÃO publica no Wiki.js nem faz commit/push sozinho — isso é passo manual
(git push + prata-kb.sh publish / deploy-manual.sh), igual o resto da KB.
Rodar isso é seguro de repetir: usa .data/wiki-promoted.json (point_id do
Qdrant já avaliados, aprovados ou não) pra nunca reavaliar o mesmo contexto
duas vezes.

Uso:
  python3 scripts/promote_to_wiki.py                  # avalia tudo que falta
  python3 scripts/promote_to_wiki.py --dry-run         # só mostra, não escreve
  python3 scripts/promote_to_wiki.py --limit 20        # teto de avaliações por run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx

QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
NINE_ROUTER_URL = os.environ.get("NINE_ROUTER_URL", "http://localhost:20128")
NINE_ROUTER_COMBO = os.environ.get("NINE_ROUTER_COMBO", "wpp-context")
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
# Judge via JEV System One (api.typesafe.ai) como alternativa ao LLM do
# 9router acima: chave dedicada do Prata (não a pessoal global de
# ~/.jev/config.json), protocolo "noul" (booleano + probabilidade) — mesmo
# contrato de jev-claude-engine/src/client.js (JevClient.evaluate),
# reimplementado aqui em Python porque este script roda fora do Node.
JEV_API_KEY = os.environ.get("JEV_API_KEY_PRATA", os.environ.get("JEV_API_KEY", ""))
JEV_ENDPOINT = os.environ.get("JEV_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
STATE_PATH = Path(__file__).resolve().parent.parent / ".data" / "wiki-promoted.json"
KB_REPO = Path(
    os.environ.get("PRATA_KB_REPO", str(Path.home() / "Documents/projects/devwiki/kb-repo"))
)
DOCS_DIR = KB_REPO / "docs"
CATEGORY_DIR = DOCS_DIR / "decisoes-wpp"
INDEX_FILE = DOCS_DIR / "decisoes-wpp.md"

JUDGE_PROMPT = """Você é um editor conservador de uma Wiki de engenharia/produto.
Vai receber um resumo de decisão extraído automaticamente de um grupo de WhatsApp
de trabalho. Decida se isso merece virar uma página PERMANENTE na Wiki (referência
futura: "como X funciona", "por que decidimos Y") ou se é só conversa efêmera de
trabalho (status de tarefa, combinação de horário, small talk, algo que já está
documentado em outro lugar como Jira/PR).

Responda SÓ em JSON: {{"promote": true|false, "reason": "frase curta"}}

Critério pra promote=true: a decisão tem impacto duradouro (muda uma regra de
negócio, arquitetura, processo, ou é um "porquê" que alguém vai perguntar de novo
no futuro). Critério pra promote=false: é status pontual, lembrete, ou chat social.

Contexto a avaliar:
Empresa/grupo: {company}
Título: {title}
Resumo: {summary}
Tópicos: {topics}
"""


def load_state() -> set[str]:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text()))
    return set()


def save_state(seen: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(seen), indent=2))


def fetch_decisao_contexts(client: httpx.Client, limit: int = 500) -> list[dict]:
    resp = client.post(
        f"{QDRANT_URL}/collections/wpp_context/points/scroll",
        json={
            "limit": limit,
            "with_payload": True,
            "filter": {"must": [{"key": "kind", "match": {"value": "decisao"}}]},
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("result", {}).get("points", [])


def judge_llm(client: httpx.Client, payload: dict) -> dict:
    """Judge via LLM genérico no 9router (combo wpp-context). Retorna
    {promote, reason}."""
    prompt = JUDGE_PROMPT.format(
        company=payload.get("company", "?"),
        title=payload.get("title", "?"),
        summary=payload.get("summary", "?"),
        topics=", ".join(payload.get("topics") or []),
    )
    resp = client.post(
        f"{NINE_ROUTER_URL}/v1/chat/completions",
        headers={"content-type": "application/json", "x-api-key": ANTHROPIC_AUTH_TOKEN},
        json={
            "model": NINE_ROUTER_COMBO,
            "max_tokens": 150,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    # 9router anexa "data: [DONE]" colado direto após o JSON mesmo fora de
    # streaming (sem separador) — usa raw_decode pra pegar só o 1º objeto
    # válido e ignorar o resto da string.
    decoder = json.JSONDecoder()
    parsed, _ = decoder.raw_decode(resp.text)
    text = parsed["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"promote": False, "reason": "judge não retornou JSON"}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"promote": False, "reason": "judge retornou JSON inválido"}


def judge_jev(client: httpx.Client, payload: dict) -> dict:
    """Judge via JEV System One (protocolo noul: booleano + probabilidade).
    Mesmo payload shape que JevClient.evaluate() em jev-claude-engine/src/client.js
    — {model: "jev-latest", state, questions}, Authorization: Bearer <key>.
    Retorna {promote, reason, probability} ou {promote: False, reason: "..."}
    em caso de falha/timeout (fail-closed: não promove no erro)."""
    if not JEV_API_KEY:
        return {"promote": False, "reason": "JEV_API_KEY_PRATA ausente"}
    state = (
        f"Empresa/grupo: {payload.get('company', '?')}\n"
        f"Título: {payload.get('title', '?')}\n"
        f"Resumo: {payload.get('summary', '?')}\n"
        f"Tópicos: {', '.join(payload.get('topics') or [])}"
    )
    questions = {
        "promote": {
            "type": "noul",
            "instructions": (
                "Esta decisão de trabalho extraída do WhatsApp merece virar "
                "página PERMANENTE numa Wiki de engenharia/produto (referência "
                "futura: 'como X funciona', 'por que decidimos Y')? Responda "
                "falso para status pontual de tarefa, lembrete ou chat social."
            ),
        }
    }
    try:
        resp = client.post(
            JEV_ENDPOINT,
            headers={"content-type": "application/json", "Authorization": f"Bearer {JEV_API_KEY}"},
            json={"model": "jev-latest", "state": state, "questions": questions},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        ans = data.get("answers", {}).get("promote", {})
        raw = ans.get("noul")
        # Mesma normalização de jev-claude-engine/src/client.js (linhas 73-76):
        # "noul" pode vir como bool OU como float cru (probabilidade) — "probability"
        # separado só aparece às vezes. Testado em 2026-11-13: api.typesafe.ai
        # devolveu {"noul": 0.22} sem campo "probability" nenhum; meu código
        # original só olhava "probability" e sempre dava promote=False (bug).
        prob = ans.get("probability")
        if prob is None and isinstance(raw, (int, float)) and not isinstance(raw, bool):
            prob = float(raw)
        promote = raw if isinstance(raw, bool) else (prob is not None and prob >= 0.5)
        return {"promote": bool(promote), "reason": f"JEV noul (prob={prob})", "probability": prob}
    except Exception as exc:  # noqa: BLE001 — fail-closed, nunca derruba o run
        return {"promote": False, "reason": f"JEV falhou: {exc}"}


def judge_compare(client: httpx.Client, payload: dict) -> tuple[dict, dict]:
    """Roda os dois judges no mesmo contexto pra comparar resultado. A decisão
    final usada pelo caller é a do LLM (mais testado); o resultado do JEV só
    é reportado para análise."""
    llm_verdict = judge_llm(client, payload)
    jev_verdict = judge_jev(client, payload)
    return llm_verdict, jev_verdict


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "decisao"


def write_page(payload: dict, point_id: str, reason: str) -> Path:
    CATEGORY_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{today}-{slugify(payload.get('title', 'decisao'))}"
    page_path = CATEGORY_DIR / f"{slug}.md"
    tags = ", ".join(payload.get("topics") or [])
    content = f"""---
title: {payload.get('title', 'Decisão')}
description: Decisão extraída do WhatsApp de trabalho ({payload.get('company', '?')})
category: decisoes-wpp
audience: operacional,administrativo,desenvolvedor
tags: [decisoes-wpp, {tags}]
---

# {payload.get('title', 'Decisão')}

**Origem:** grupo/contato `{payload.get('company', '?')}` (WhatsApp) · indexado automaticamente via wpp-context-index
**Motivo da promoção:** {reason}

## Resumo

{payload.get('summary', '')}

## Tópicos

{', '.join(payload.get('topics') or []) or '(nenhum)'}

---
_Gerado automaticamente a partir de `wpp_context` (point_id `{point_id}`). Revise antes de considerar definitivo._
"""
    page_path.write_text(content, encoding="utf-8")
    return page_path


def update_index(entries: list[tuple[str, str]]) -> None:
    """entries: list of (title, relative_slug) to append to the index page."""
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text(
            """---
title: Decisões (WhatsApp)
description: Decisões de trabalho extraídas automaticamente do WhatsApp (wpp-context-index) e promovidas pra Wiki após revisão de um LLM-judge conservador.
category: decisoes-wpp
audience: operacional,administrativo,desenvolvedor
tags: [decisoes-wpp, whatsapp, decisoes]
---

# Decisões (WhatsApp)

Decisões identificadas automaticamente em conversas de trabalho no WhatsApp,
avaliadas como relevantes o suficiente pra virar referência permanente.
Revise sempre antes de considerar definitivo — a extração é automática.

## Registros

"""
        )
    with INDEX_FILE.open("a", encoding="utf-8") as f:
        for title, slug in entries:
            f.write(f"- [{title}](/pt-br/decisoes-wpp/{slug})\n")


def run_compare(args) -> int:
    """Modo --compare: roda os dois judges (LLM 9router vs JEV System One) nos
    mesmos contextos SÓ pra avaliar qual acerta mais — nunca escreve página,
    nunca marca como visto (não consome o state real). Uso pontual, não é o
    fluxo de produção."""
    client = httpx.Client()
    points = fetch_decisao_contexts(client)[: args.limit]
    print(f"comparando os 2 judges em {len(points)} contexto(s):\n")
    agree = 0
    for p in points:
        payload = p.get("payload", {})
        llm_v, jev_v = judge_compare(client, payload)
        same = llm_v.get("promote") == jev_v.get("promote")
        agree += int(same)
        mark = "✅" if same else "⚠️ DIVERGE"
        print(f"{mark} {payload.get('title', '?')[:60]}")
        print(f"    LLM: promote={llm_v.get('promote')} — {llm_v.get('reason')}")
        print(f"    JEV: promote={jev_v.get('promote')} — {jev_v.get('reason')}")
    print(f"\nconcordância: {agree}/{len(points)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=50, help="teto de avaliações por run")
    parser.add_argument(
        "--compare", action="store_true",
        help="roda LLM e JEV lado a lado nos mesmos contextos pra decidir qual usar — não promove nada, não marca como visto",
    )
    args = parser.parse_args()

    if not ANTHROPIC_AUTH_TOKEN:
        print("ANTHROPIC_AUTH_TOKEN ausente — exporte antes de rodar.", file=sys.stderr)
        return 1

    if args.compare:
        return run_compare(args)

    seen = load_state()
    client = httpx.Client()
    points = fetch_decisao_contexts(client)
    todo = [p for p in points if p["id"] not in seen][: args.limit]

    print(f"contextos kind=decisao no Qdrant: {len(points)} | ainda não avaliados: {len(todo)}")

    promoted: list[tuple[str, str]] = []
    for p in todo:
        pid = p["id"]
        payload = p.get("payload", {})
        # Critério combinado (AND): só promove se os DOIS judges concordarem
        # que é relevante — testado em 2026-11-13, 7/8 concordância entre LLM
        # (9router) e JEV System One; o único caso de divergência real era
        # ambíguo o suficiente pra preferir o lado conservador (prioriza
        # precisão sobre recall — melhor perder um caso de fronteira do que
        # poluir a Wiki com status pontual).
        llm_v = judge_llm(client, payload)
        jev_v = judge_jev(client, payload)
        promote = bool(llm_v.get("promote")) and bool(jev_v.get("promote"))
        reason = "; ".join(
            r for r in (
                f"LLM: {llm_v.get('reason')}" if llm_v.get("reason") else None,
                f"JEV: {jev_v.get('reason')}" if jev_v.get("reason") else None,
            ) if r
        )
        verdict = {"promote": promote, "reason": reason}
        seen.add(pid)
        if verdict.get("promote"):
            if args.dry_run:
                print(f"  [DRY] promoveria: {payload.get('title')} — {verdict.get('reason')}")
            else:
                page_path = write_page(payload, pid, verdict.get("reason", ""))
                slug = page_path.stem
                promoted.append((payload.get("title", "Decisão"), slug))
                print(f"  ✅ promovido: {payload.get('title')} -> {page_path.relative_to(KB_REPO)}")
        else:
            print(f"  ⏭️  descartado: {payload.get('title')} — {verdict.get('reason')}")

    if promoted and not args.dry_run:
        update_index(promoted)
        print(f"\n{len(promoted)} página(s) nova(s). Próximo passo manual:")
        print(f"  cd {KB_REPO} && git add -A && git commit -m 'feat: decisões do WhatsApp' && git push")
        print(f"  bash {KB_REPO}/scripts/deploy-manual.sh --no-build")

    if not args.dry_run:
        save_state(seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
