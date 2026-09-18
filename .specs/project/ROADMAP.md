# Roadmap

**Current Milestone:** M2 — Multimodal
**Status:** Planning (M0+M1 done, código-completo em T1-T15)

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
- Embedding (`mxbai-embed-large`) + upsert idempotente no Qdrant

**Busca** - DONE (T14-T15)

- `POST /api/v1/search` + `wpp-context search`: embedding local + filtro
  payload + boost lexical pra identificador exato

---

## M2 — Multimodal

**Goal:** Encontrar imagem/áudio/vídeo através de texto (PRD Fase 3).

### Features

**Imagem** - PLANNED

- Descrição via Ollama vision (`gemma4:12b`) direto no pipeline batch

**Áudio/vídeo** - PLANNED

- Reuso de `transcribe-audio-video` (subprocess `--frames --format json`)
- Cleanup garantido de mídia temporária

---

## M3 — Source resolver & hardening

**Goal:** Resultado de busca leva à evidência original; MVP robusto pra
uso diário (PRD Fases 4-5).

### Features

**Source resolver** - PLANNED

- `GET /api/v1/source/{id}` e `GET /api/v1/source/media/{message_id}`
  (sob demanda via WPP Bot Server, sem cache permanente)

**Hardening** - PLANNED

- Retry idempotente, testes unit+integração, limites de tamanho/timeout,
  logs estruturados sem conteúdo sensível, scheduler (cron do host, sem
  Celery/Redis)

---

## Future Considerations

- Busca federada Wiki + WPP Context Index (roteador)
- `promote_to_wiki` com aprovação humana
- Reranker local, feedback de busca, MCP server
- Indexação de PDFs/documentos completos
