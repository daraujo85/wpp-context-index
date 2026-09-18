# WPP Context Index

**Vision:** Ferramenta local que transforma conversas corporativas do
WhatsApp (Prata Digital) em um índice pesquisável por linguagem natural,
com evidência e rastreabilidade até a mensagem original.
**For:** Time Prata Digital e agentes internos (ex: Pratinha) que
precisam achar contexto operacional já discutido no WhatsApp sem reler
histórico.
**Solves:** Conhecimento operacional (bugs, decisões, mockups, links)
se perde em conversas de WhatsApp; não há busca semântica sobre isso hoje.

## Goals

- Responder perguntas em linguagem natural ("onde mandaram o mockup de
  cancelamento?") com evidência (grupo/DM, autor, data, mensagem de
  origem) — medido pelos 9 cenários de aceite do PRD (§22).
- Ingestão em batch local, sem LLM externa, sem armazenar mídia
  permanentemente — medido por: 0 chamadas a API de IA externa no
  pipeline, 0 mídia residual após processamento.
- Reprocessar o mesmo período não duplica registros — medido por
  contagem de pontos no Qdrant antes/depois de reingerir o mesmo range.

## Tech Stack

**Core:**

- Framework: FastAPI (Python)
- Language: Python 3.11+
- Database: Qdrant (local, dedicado, via docker-compose)

**Key dependencies:**

- `qdrant-client` — vetores/payload/filtros
- Ollama (HTTP, local, `localhost:11434` do host — `host.docker.internal:11434`
  quando o serviço roda dentro do container `wpp-context`, ver
  `docker-compose.yml`) — embedding
  (`nomic-embed-text`) e visão (`gemma4:12b`), modelos já existentes,
  configuráveis via `.env`
- skill `whatsapp-message` (`wpp.sh`, subprocess) — leitura de
  chats/mensagens/mídia do WPP Bot Server
- skill `transcribe-audio-video` (`transcribe_audio.py`, subprocess) —
  transcrição de áudio/vídeo + extração de frames

## Scope

**v1 includes:**

- Ingestão em batch (CLI + endpoint admin) por intervalo de datas, sobre
  as fontes já allowlisted em `~/.claude/secrets/whatsapp.env` +
  `whatsapp-lids.json`
- Pipeline: normalização → pré-filtro determinístico → agrupamento em
  unidades de contexto → multimodal (texto/imagem/áudio/vídeo) → Guard
  Rail de privacidade/relevância → extração estruturada → embedding →
  upsert idempotente no Qdrant
- Busca (API + CLI): embedding local + filtro payload + boost lexical
  pra identificadores exatos, sem LLM generativa
- Resolução de origem: endpoint que devolve mensagens/mídia original sob
  demanda via WPP Bot Server (sem cache permanente)

**Explicitly out of scope:**

- UI web, chatbot, WhatsApp em tempo real/webhook
- PostgreSQL, Elasticsearch, Redis, Kafka, Celery
- Qualquer LLM/API de IA externa (OpenAI, Gemini, Anthropic, etc.) no
  pipeline
- Armazenamento permanente de mídia
- Integração automática com a Wiki/RAG existente (`promote_to_wiki` é
  evolução futura, aprovação humana)
- MCP server (adapter futuro sobre a FastAPI, opcional)

## Constraints

- Timeline: POC/MVP, sem deadline formal declarado.
- Technical: local-first obrigatório; não subir 2ª instância de Ollama;
  não baixar modelo novo automaticamente; `INGEST_CONCURRENCY=1`,
  `MEDIA_CONCURRENCY=1` por padrão; bind FastAPI em `127.0.0.1`.
- Resources: reuso obrigatório de skills existentes
  (`whatsapp-message`, `transcribe-audio-video`) antes de qualquer
  implementação nova equivalente (regra do PRD §29).
