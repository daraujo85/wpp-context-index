# Roadmap

**Current Milestone:** MVP completo — sem próximo milestone nomeado
**Status:** M0+M1+M2+M3 done, código-completo em T1-T25. Fases 0-5 do PRD
(§25) encerradas. Próximo trabalho só via pedido explícito do usuário
(ver "Future Considerations" abaixo / PRD §27 "Evoluções possíveis").

---

## M0 — Esqueleto & Discovery ✅ DONE

**Goal:** Confirmar contratos reais das skills reusadas e ter FastAPI +
CLI + Qdrant + adapter WPP funcionando ponta a ponta (sem pipeline
semântico ainda).
**Target:** listar chats e buscar mensagens por período através do
adapter (PRD Fase 1, gate). Atingido via T1-T7.

### Features

**Discovery formal** - DONE (T1, T2)

- Registrar contratos de `wpp.sh`, `transcribe_audio.py`, Ollama, Qdrant
  em `.specs/codebase/` (brownfield mapping das skills externas)
- Estender `wpp.sh` com comando `history <target> --start --end`

**Esqueleto do serviço** - DONE (T3-T7)

- FastAPI (`/health`, `/ready`), config lendo `whatsapp.env` +
  `whatsapp-lids.json` + `.env` do projeto
- `docker-compose` com `wpp-context` + `qdrant` local dedicado
- CLI fino (`wpp-context`) sobre as mesmas services da API

---

## M1 — Ingestão de texto ponta a ponta ✅ DONE

**Goal:** Busca semântica funcionando pra mensagens de texto (PRD Fase 2).
Código-completo via `tasks.md` T8-T15 (commits `d9ee658`..`94adaeb`).

### Features

**Pipeline de texto** - DONE (T8-T13)

- Normalização, pré-filtro determinístico, agrupamento por
  chat+janela+reply
- Guard Rail (1 chamada LLM local) + extração estruturada
- Embedding (`nomic-embed-text`) + upsert idempotente no Qdrant

**Busca** - DONE (T14-T15)

- `POST /api/v1/search` + `wpp-context search`: embedding local + filtro
  payload + boost lexical pra identificador exato

---

## M2 — Multimodal ✅ DONE

**Goal:** Encontrar imagem/áudio/vídeo através de texto (PRD Fase 3).
Código-completo via `tasks-m2.md` T16-T21.

### Features

**Imagem** - DONE (T16)

- Descrição via Ollama vision (`gemma4:12b`) direto no pipeline batch

**Áudio/vídeo** - DONE (T17)

- Reuso de `transcribe-audio-video` (subprocess `--frames --format json`)
- Cleanup garantido de mídia temporária (T21)

**Pipeline fim-a-fim** - DONE (T18-T21)

- Guard Rail extraí `artifacts`, embedding inclui descrições, payload
  Qdrant ganha `media[]`, `ingestion.py` processa imagem/áudio/vídeo por
  unidade com status `failed_media` isolado por unidade

---

## M3 — Source resolver & hardening ✅ DONE

**Goal:** Resultado de busca leva à evidência original; MVP robusto pra
uso diário (PRD Fases 4-5). Código-completo via `tasks-m3.md` T22-T25.

### Features

**Source resolver** - DONE (T22)

- `GET /api/v1/source/{id}` e `GET /api/v1/source/media/{message_id}`
  (sob demanda via `wpp.get_media`, sem cache permanente — cleanup
  garantido via `BackgroundTask` após a resposta)
- Deliberadamente fora de escopo: `GET /api/v1/context/{id}` (PRD §13
  o lista como "mínimo sugerido", mas não está nos critérios rastreáveis
  do spec.md P3) — gap conhecido, não construído

**Hardening** - DONE (T23-T25)

- Limite de tamanho de mídia + validação de MIME (`MEDIA_MAX_SIZE_MB`,
  `validate_mime()`, isolados via `failed_media`) — T23
- Idempotência de reingestão comprovada também pra unidades com mídia,
  sem mecanismo de retry novo (upsert por ID determinístico já basta) —
  T24
- Scheduler: `scripts/ingest-yesterday.sh` + exemplo de crontab no
  README, sem Celery/Redis — T25
- Timeout em subprocess, validação de resposta da skill e observabilidade
  (`IngestionSummary`) já estavam satisfeitos por código de M1/M2 —
  confirmado por leitura direta, sem task nova

---

## Future Considerations

- Busca federada Wiki + WPP Context Index (roteador)
- `promote_to_wiki` com aprovação humana
- Reranker local, feedback de busca, MCP server
- Indexação de PDFs/documentos completos
