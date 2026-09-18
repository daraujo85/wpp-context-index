# wpp-context-index

Índice/busca de contexto sobre mensagens WhatsApp (WPP), com ingestão
multimodal (texto/mídia) para Qdrant. Ver `docs/PRD.md` para o produto
completo.

## Setup

```bash
cp .env.example .env   # ajuste OLLAMA_BASE_URL/QDRANT_URL etc.
docker compose up -d
```

## Uso da CLI

```bash
python -m app.cli ingest --start 2026-09-01 --end 2026-09-18
python -m app.cli search "onde mandaram o mockup?"
```

## Agendamento (ingestão diária via cron do host)

PRD §18 proíbe Celery/Redis/scheduler novo. A ingestão diária roda via
cron do host chamando `scripts/ingest-yesterday.sh`, que calcula a janela
"ontem 00:00 até hoje 00:00" (fuso local) e invoca a CLI `ingest` (T7)
sem modificá-la.

Exemplo de linha de crontab (roda às 03:00, por sugestão do PRD §18):

```cron
0 3 * * * cd /caminho/para/wpp-context-index && ./scripts/ingest-yesterday.sh >> /var/log/wpp-context-ingest.log 2>&1
```
