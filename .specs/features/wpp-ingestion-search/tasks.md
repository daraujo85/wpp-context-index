# WPP Ingestion & Search Tasks

**Design**: `.specs/features/wpp-ingestion-search/design.md`
**Status**: M0+M1 done (T1-T15 code-complete)
**Escopo desta rodada**: M0 (Esqueleto & Discovery) + M1 (texto, stories
P1 do spec — busca semântica de texto + Guard Rail de privacidade). M2
(multimodal) e M3 (source resolver/hardening) ganham `tasks.md` próprio
depois que M1 estiver validado — o formato real de `wpp.sh history`
(T2) e do Guard Rail (T9) pode mudar decisões de M2/M3.

**Tools por task**: MCP nenhum necessário (projeto sem `.codegraph/`).
Skills externas usadas via subprocess: `whatsapp-message` (`wpp.sh`,
T2/T8) e `transcribe-audio-video` (fora do escopo desta rodada, entra em
M2). Nenhuma MCP/skill precisa de confirmação adicional — já definidas
no design.

---

## Execution Plan

### Phase 1: Foundation (Sequential)

```
T1 → T2 → T3 → T4
```

### Phase 2: Skeleton (Parallel OK após T4)

```
        ┌→ T5 ─┐
T4 ─────┼→ T6 ─┼──→ T7
        └──────┘
```

### Phase 3: Text pipeline (Sequential, depende de Phase 2)

```
T7 → T8 → T9 → T10 → T11 → T12 → T13
```

### Phase 4: Search (depende de T13)

```
T13 → T14 → T15
```

### Cross-check (diagram vs Depends on)

| Task | Diagram depends on | `Depends on` field |
|---|---|---|
| T1 | — | None |
| T2 | T1 | T1 |
| T3 | T2 | T2 |
| T4 | T3 | T3 |
| T5 | T4 | T4 |
| T6 | T4 | T4 |
| T7 | T5, T6 | T5, T6 |
| T8 | T7 | T7 |
| T9 | T8 | T8 |
| T10 | T9 | T9 |
| T11 | T10 | T10 |
| T12 | T11 | T11 |
| T13 | T12 | T12 |
| T14 | T13 | T13 |
| T15 | T14 | T14 |

### Test co-location check

| Task | Layer | Required test (TESTING.md) | Task's `Tests` field | OK? |
|---|---|---|---|---|
| T1-T3 | config/scaffolding | none | none | ✅ |
| T4 | adapters (wpp.py) | integration | integration | ✅ |
| T5 | api (health/ready) | integration | integration | ✅ |
| T6 | infra (compose) | none | none | ✅ |
| T7 | cli | integration | integration | ✅ |
| T8 | domain (messages.py) | unit | unit | ✅ |
| T9 | domain (contexts.py) | unit | unit | ✅ |
| T10 | services (grouping.py) | unit | unit | ✅ |
| T11 | domain (guardrail.py) + adapters (inference.py) | unit + integration | unit + integration | ✅ |
| T12 | services (embedding.py) + adapters (inference.py, qdrant.py) | integration | integration | ✅ |
| T13 | services (ingestion.py) | integration | integration | ✅ |
| T14 | services (search.py) | unit | unit | ✅ |
| T15 | api (search.py, ingest.py) | integration | integration | ✅ |

---

## Task Breakdown

### T1: Config lendo whatsapp.env + whatsapp-lids.json + .env do projeto

**What**: `app/config.py` — `Settings` (pydantic) que lê
`~/.claude/secrets/whatsapp.env` (paths configuráveis, default esse),
`whatsapp-lids.json`, e `.env` do projeto (`VISION_MODEL`,
`EMBEDDING_MODEL`, `OLLAMA_BASE_URL`, `QDRANT_URL`,
`INGEST_CONCURRENCY`, `MEDIA_CONCURRENCY`, `CONVERSATION_GAP_MINUTES`).
**Where**: `app/config.py`, `.env.example`
**Depends on**: None
**Reuses**: convenção `WPP_CONTACT_*`/`WPP_GROUP_*` já existente

**Done when**:
- [x] `Settings.sources()` devolve lista de `{alias, jid, is_group}` a
      partir do `whatsapp.env` real
- [x] Nenhum valor de modelo hard-coded — tudo com default vindo do env
- [x] `.env.example` documenta cada var (sem segredo real)

**Tests**: none (config só é validado pelos testes que a consomem)
**Gate**: quick

---

### T2: Estender `wpp.sh` com comando `history`

**What**: comando novo `history <target> --start <iso> --end <iso>
[--limit N]` na skill `whatsapp-message`, sobre
`getAllMessagesByContactId` com `startDate`/`endDate`, JSON de saída.
**Where**: `~/.claude/skills/whatsapp-message/wpp.sh` (fora deste repo —
skill compartilhada), `SKILL.md` atualizado com o comando novo
**Depends on**: T1 (sabe o formato de `target`/allowlist esperado)
**Reuses**: roteamento `@g.us`/`@c.us`/`@lid` já existente no `wpp.sh`

**Done when**:
- [x] `wpp.sh history <alias-ou-jid> --start ... --end ...` devolve JSON
      com mensagens (id, sender, t, type, body, quotedMsgId quando houver)
- [x] Chat sem mensagem no intervalo devolve lista vazia (não erro)
- [x] `SKILL.md` documenta o comando novo (tabela de endpoints)

**Tests**: integration (chamada real contra `WPP_GROUP_PRATINHA_INBOX`,
intervalo curto conhecido)
**Gate**: full

---

### T3: Adapter WPP — client sobre `wpp.sh`

**What**: `app/adapters/wpp.py` com `chats()`, `history(target, start,
end)`, `get_media(message_id)` — subprocess sobre `wpp.sh`, parse do
JSON, erro tipado se `wpp.sh` falhar.
**Where**: `app/adapters/wpp.py`
**Depends on**: T2
**Reuses**: `wpp.sh chats`/`get-media` já existentes + `history` novo (T2)

**Done when**:
- [x] `history()` devolve lista de dicts normalizados (ou vazia)
- [x] `get_media()` devolve `(path, mime, size)` e propaga erro se
      `wpp.sh` retornar `{"data": null}`
- [x] Timeout configurável em toda chamada subprocess

**Tests**: integration
**Gate**: full

---

### T4: Domain — envelope de mensagem normalizado [P após T3, mas sequencial no plano]

**What**: `app/domain/messages.py` — `normalize(raw_wpp_message) ->
MessageEnvelope` (PRD §6.1: company, chat_id, chat_name, is_group,
message_id, sender_id, sender_name, timestamp, type, body,
quoted_message_id, has_media).
**Where**: `app/domain/messages.py`
**Depends on**: T3 (formato real de saída do adapter)
**Reuses**: nenhuma lib nova — `dataclass`/pydantic já em uso

**Done when**:
- [x] `normalize()` cobre os 5 tipos (text/image/audio/video/document)
- [x] Campos desconhecidos ficam `None`, nunca quebra
- [x] Unit test com fixture de cada tipo de mensagem real do `wpp.sh`

**Tests**: unit
**Gate**: quick

---

### T5: FastAPI — health/ready [P]

**What**: `GET /health` (sempre 200) e `GET /ready` (checa Qdrant +
adapter WPP, sem carregar modelo pesado).
**Where**: `app/api/__init__.py` ou `app/api/health.py`, `app/main.py`
**Depends on**: T4
**Reuses**: `app/adapters/qdrant.py` (client básico), `app/adapters/wpp.py`

**Done when**:
- [x] `/health` responde 200 sem dependência externa
- [x] `/ready` responde 503 se Qdrant ou WPP estiverem fora, com motivo

**Tests**: integration (TestClient FastAPI)
**Gate**: full

---

### T6: Docker Compose — wpp-context + qdrant local [P]

**What**: `docker-compose.yml` com serviço `wpp-context` (bind
`127.0.0.1`) + `qdrant` dedicado (volume nomeado), `Dockerfile`.
**Where**: `docker-compose.yml`, `Dockerfile`, `.dockerignore`
**Depends on**: T4
**Reuses**: rede/endpoint existente do Ollama (host, via
`OLLAMA_BASE_URL`, sem subir Ollama no compose)

**Done when**:
- [x] `docker compose up` deixa `/health` e Qdrant respondendo
- [x] Diretório de mídia temporária é efêmero (tmpfs ou volume anônimo)

**Tests**: none (validação manual/CI de smoke, sem teste automatizado
próprio)
**Gate**: none

---

### T7: CLI fino sobre as services

**What**: `app/cli/__main__.py` — comandos `wpp-context ingest
--start/--end`, `search "<query>"`, `inspect <id>`, `source <id>` (thin
wrapper, sem lógica própria).
**Where**: `app/cli/__main__.py`
**Depends on**: T5, T6
**Reuses**: mesmas `services/*` que a API vai usar (T13-T15)

**Done when**:
- [x] `wpp-context ingest --start ... --end ...` roda e imprime resumo
      de execução (mesmo que services ainda sejam stub nesta fase)
- [x] CLI não duplica lógica — só parseia args e chama service

**Tests**: integration
**Gate**: full

**Gate da Phase 1-2**: listar chats e buscar mensagens por período
através do adapter (PRD Fase 1) — critério de saída do M0.

---

### T8: Domain — pré-filtro determinístico

**What**: `app/domain/messages.py` (extensão) — `should_discard(msg) ->
bool` cobrindo sistema/vazio/duplicata/status/stickers sem contexto +
`redact(text) -> (text, found_secrets: bool)` pra
token/senha/OTP/chave.
**Where**: `app/domain/messages.py`
**Depends on**: T7
**Reuses**: `MessageEnvelope` de T4

**Done when**:
- [x] Cada categoria do PRD §6.2 tem 1 caso de teste
- [x] `redact()` nunca deixa o segredo original no texto retornado

**Tests**: unit
**Gate**: quick

---

### T9: Domain — unidade de contexto + ID determinístico

**What**: `app/domain/contexts.py` — `ContextUnit` (dataclass) +
`deterministic_id(company, chat_id, message_ids, extractor_version) ->
str` (hash estável, mesma entrada = mesma saída).
**Where**: `app/domain/contexts.py`
**Depends on**: T8
**Reuses**: nenhuma lib nova (`hashlib` stdlib)

**Done when**:
- [x] Mesmo input sempre gera o mesmo ID (assert em teste)
- [x] Ordem dos `message_ids` não muda o hash (normaliza/ordena antes)

**Tests**: unit
**Gate**: quick

---

### T10: Services — agrupamento em unidades de contexto

**What**: `app/services/grouping.py` — `group(messages: list[MessageEnvelope],
gap_minutes: int) -> list[ContextUnit]`: mesmo chat + janela temporal +
replies/citações + teto de tamanho configurável.
**Where**: `app/services/grouping.py`
**Depends on**: T9
**Reuses**: `CONVERSATION_GAP_MINUTES` de T1, `ContextUnit` de T9

**Done when**:
- [x] Sequência de mensagens próximas no tempo vira 1 unidade
      (fixture do exemplo do PRD §7)
- [x] Reply fora da janela temporal ainda agrupa com a mensagem citada
- [x] Não agrupa um dia inteiro num único bloco (teto de tamanho testado)

**Tests**: unit
**Gate**: quick

---

### T11: Domain + adapter — Guard Rail combinado com extração

**What**: `app/domain/guardrail.py` (schema pydantic combinando §8.1 +
§9 do PRD, parser da resposta do LLM) + `app/adapters/inference.py`
método `classify_and_extract(unit_text) -> GuardrailResult` (chamada
Ollama `TEXT_MODEL`, JSON mode).
**Where**: `app/domain/guardrail.py`, `app/adapters/inference.py`
**Depends on**: T10
**Reuses**: `OLLAMA_BASE_URL` de T1

**Done when**:
- [x] Schema único devolve `decision`, `work_relevance`,
      `contains_personal_content`, `redactions`, `title`, `summary`,
      `kind`, `topics` numa chamada
- [x] Parser rejeita/normaliza resposta malformada do LLM sem crashar o
      batch (fallback: `decision=discard`)
- [x] Unit test com resposta LLM mockada (JSON fixo) cobrindo os 2
      cenários do PRD §8.1

**Tests**: unit (parser) + integration (chamada real ao Ollama local)
**Gate**: full

---

### T12: Services — embedding + upsert Qdrant

**What**: `app/services/embedding.py` (`build_embedding_text(unit) ->
str` + chama `inference.py.embed()`) e `app/adapters/qdrant.py`
(`upsert(point)` com payload mínimo do PRD §10.2, ID de T9).
**Where**: `app/services/embedding.py`, `app/adapters/qdrant.py`
**Depends on**: T11
**Reuses**: `EMBEDDING_MODEL` de T1, `deterministic_id` de T9

**Done when**:
- [x] Texto de embedding usa só título+resumo+topics+artifacts (nunca
      metadado bruto)
- [x] `upsert()` do mesmo ID duas vezes não duplica ponto no Qdrant
      (teste de integração reingesta o mesmo fixture 2x, conta pontos)
- [x] Payload não contém base64/binário/conversa pessoal rejeitada

**Tests**: integration (Qdrant local real)
**Gate**: full

---

### T13: Services — ingestion orquestrando o pipeline fim-a-fim

**What**: `app/services/ingestion.py` — `run(start, end) ->
IngestionSummary`: itera fontes allowlisted (T1), chama T3.history por
fonte, pula fonte vazia sem escrever nada, roda T8→T9→T10→T11→T12 por
unidade, captura erro por unidade sem abortar o batch, agrega métricas
do PRD §19.1.
**Where**: `app/services/ingestion.py`
**Depends on**: T12
**Reuses**: todas as peças de T1-T12

**Done when**:
- [x] Fonte sem mensagem no período: zero chamada além de `history`,
      zero escrita (teste cobre isso explicitamente)
- [x] Erro numa unidade (ex: LLM indisponível) marca `failed_guardrail`
      e o batch continua pra próxima unidade
- [x] `IngestionSummary` bate exatamente os campos do PRD §19.1
- [x] Reingerir o mesmo `start`/`end` não duplica pontos no Qdrant

**Tests**: integration (fixture sintética completa: texto relevante,
texto pessoal, texto misto, chat vazio)
**Gate**: full

**Gate da Phase 3**: pipeline de texto ponta a ponta rodando sobre
fixtures — critério de saída do M1 (ingestão).

---

### T14: Services — busca (embedding + filtro + boost lexical)

**What**: `app/services/search.py` — `search(query, filters, limit) ->
list[SearchResult]`: embedding da query, busca dense no Qdrant, detecta
padrão de identificador exato (regex URL/ticket/hash) e aplica boost
lexical em `topics`/`title`/`urls`.
**Where**: `app/services/search.py`
**Depends on**: T13
**Reuses**: `EMBEDDING_MODEL`/`inference.py` de T11-T12, `qdrant.py` de T12

**Done when**:
- [x] Busca por paráfrase encontra unidade indexada por texto diferente
      (fixture de T13)
- [x] Query com identificador exato (ex: `ABC-123`) prioriza unidade com
      esse ticket no payload sobre unidade só semanticamente próxima
- [x] Nenhuma chamada a LLM generativa dentro de `search()`

**Tests**: unit (com Qdrant/inference mockados) + integration (Qdrant
real com fixtures de T13 já indexadas)
**Gate**: full

---

### T15: API — `POST /api/v1/search` e `POST /api/v1/ingest`

**What**: `app/api/search.py` e `app/api/ingest.py` — endpoints thin
sobre T13/T14, JSON determinístico (PRD §11.4), `GET
/api/v1/ingest/{run_id}` pra status.
**Where**: `app/api/search.py`, `app/api/ingest.py`
**Depends on**: T14
**Reuses**: `services/ingestion.py` (T13), `services/search.py` (T14)

**Done when**:
- [x] `POST /api/v1/search` devolve exatamente o shape do PRD §11.4
- [x] `POST /api/v1/ingest` dispara o batch (síncrono ou background
      task simples — sem Celery) e `GET /api/v1/ingest/{run_id}` reflete
      status
- [x] CLI (T7) passa a chamar essas mesmas services, não duplica lógica

**Tests**: integration (TestClient)
**Gate**: full

**Gate da Phase 4**: os 4 primeiros cenários de aceite do PRD §22
(texto, privacidade, misto, reprocessamento) demonstráveis via API/CLI —
critério de saída do M1 (busca).

---

## Próximas rodadas (fora desta tasks.md)

- **M2 (multimodal)**: `tasks.md` novo depois que T13 estiver validado
  com dados reais — inclui `adapters/transcription.py`, chamada Ollama
  vision em `inference.py`, extensão de T11 pra unidades com mídia.
- **M3 (source resolver + hardening)**: `tasks.md` novo depois de M2 —
  `api/source.py`, retry, testes de carga leve, scheduler (cron).
