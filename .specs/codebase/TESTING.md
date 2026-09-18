# Testing

Greenfield project. Framework: **pytest**.

## Test Coverage Matrix

| Layer | Test type | Parallel-safe? |
|---|---|---|
| `domain/*` (normalização, IDs, redaction, guardrail parsing, ranking) | unit | Yes |
| `adapters/*` (wpp, transcription, inference, qdrant) | integration (stub subprocess / real Qdrant local) | No (compartilham container Qdrant/estado) |
| `services/*` (ingestion, grouping, extraction, embedding, search) | unit (com adapters mockados) | Yes |
| `api/*` | integration (TestClient FastAPI) | No |

## Gate check commands

- **quick** (unit, `domain/` e `services/` com mocks): `pytest tests/unit -q`
- **full** (integração, requer `docker compose up qdrant` de pé):
  `pytest tests -q`

## Fixtures

Sintéticas em `tests/fixtures/`: texto, imagem simulada, áudio mock,
vídeo mock, link, reply, mensagem pessoal, mensagem mista. Nunca dados
reais de conversa (PRD §23).
