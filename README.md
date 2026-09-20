# wpp-context-index

Índice/busca de contexto sobre mensagens WhatsApp (WPP), com ingestão
multimodal (texto/mídia) para Qdrant. Ver `docs/PRD.md` para o produto
completo.

## Setup

```bash
cp .env.example .env   # ajuste WPP_OLLAMA_URL/QDRANT_URL etc.
docker compose up -d
```

## Uso da CLI

```bash
python -m app.cli ingest --start 2026-09-01 --end 2026-09-18   # TUI rich com progresso ao vivo
python -m app.cli search "onde mandaram o mockup?"
```

## Uso da API

```bash
docker compose up -d   # ou: uvicorn app.main:app --reload

# dispara ingestão (assíncrona, roda em background) — devolve run_id
curl -sX POST localhost:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"start": "2026-09-01T00:00:00", "end": "2026-09-18T00:00:00"}'
# → {"run_id": "...", "status": "running"}

# consulta status/resultado (poll até status != "running")
curl -s localhost:8000/api/v1/ingest/<run_id>
# → {"run_id": "...", "status": "completed", "summary": {...}}

# busca
curl -sX POST localhost:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "onde mandaram o mockup?", "limit": 5}'
```

`POST /api/v1/ingest` só devolve `run_id` — sem progresso incremental
(a TUI ao vivo é exclusiva da CLI). `filters`/`limit` em `/search` são
opcionais.

## Agendamento (ingestão diária via cron do host)

PRD §18 proíbe Celery/Redis/scheduler novo. A ingestão diária roda via
cron do host chamando `scripts/ingest-yesterday.sh`, que calcula a janela
"ontem 00:00 até hoje 00:00" (fuso local) e invoca a CLI `ingest` (T7)
sem modificá-la.

Exemplo de linha de crontab (roda às 03:00, por sugestão do PRD §18):

```cron
0 3 * * * cd /caminho/para/wpp-context-index && ./scripts/ingest-yesterday.sh >> /var/log/wpp-context-ingest.log 2>&1
```
