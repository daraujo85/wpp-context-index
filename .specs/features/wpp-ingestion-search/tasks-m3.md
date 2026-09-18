# WPP Ingestion & Search Tasks — M3 (Source resolver & hardening)

**Design**: `.specs/features/wpp-ingestion-search/design.md` (§ "Source
resolver")
**Spec**: `.specs/features/wpp-ingestion-search/spec.md` (P3: Recuperação
da origem & consumo por agente)
**Depends on**: M2 done (T16-T21, commits `61658ae`..`b175adc`)
**Status**: T22-T25 done (M3 code-complete)

**Escopo desta rodada**: PRD Fases 4-5 — source resolver (§12, §13, §22
Cenário 8) + hardening real (§18-21, §22 Cenário 7/9). Continua a
numeração a partir de T22.

**Discovery já feita** (lendo PRD + código real, não a partir da memória
do design.md):

- PRD §13 lista `GET /api/v1/context/{id}` como endpoint "mínimo
  sugerido", mas **spec.md's P3 (a fonte rastreável de requisitos deste
  projeto) não o inclui** nos critérios de aceite — só
  `/source/{id}`/`/source/media/{message_id}` (já cobertos por
  `POST /api/v1/search`, T15). Decisão: **não construir
  `/context/{id}` nesta rodada** — não é um requisito rastreado, seria
  escopo além do especificado. Fica registrado como gap conhecido, não
  como task.
- `IngestionSummary` (T13/T21) **já bate exatamente** o schema do PRD
  §19.1 (`run_id, messages_read, prefilter_discarded,
  guardrail_discarded, contexts_indexed, images_processed,
  audio_processed, videos_processed, errors, duration_seconds`) — lido
  direto em `app/services/ingestion.py`. **Observabilidade não precisa
  de task nova.**
- `app/adapters/wpp.py` e `app/adapters/transcription.py` **já têm
  timeout em toda chamada subprocess** (`DEFAULT_TIMEOUT_SECONDS`, lido
  direto no código) — PRD §21's "timeout em chamadas" já satisfeito, sem
  task nova.
- `transcription.py.transcribe()` **já valida a resposta da skill**
  (checa `data.get("success")`, `TranscriptionError` em JSON malformado)
  — PRD §21's "validar resposta da skill" já satisfeito, sem task nova.
- **Gaps reais confirmados** (o que ainda falta, lendo `wpp.py` linha a
  linha): `get_media()` não tem limite de tamanho de mídia nem valida
  MIME contra o `type` declarado da mensagem — os dois itens do PRD §21
  ("limites de tamanho para mídia", "validar MIME") que ainda não
  existem. Vira T23.
- Bind `127.0.0.1` já é o default do `docker-compose` (T6) — "autenticar
  FastAPI caso saia de localhost" (PRD §21) não se aplica: não estamos
  saindo de localhost neste MVP. Sem task.
- "Retry deve ser simples e idempotente" (PRD §20): o upsert já é
  idempotente por ID determinístico (T9/T12) — não precisa de mecanismo
  novo, só falta um teste que prove isso *incluindo* unidades com mídia
  (T21 só testou idempotência pra texto). Vira T24 (teste, não código
  novo, a menos que o teste revele um gap real).
- Scheduler (PRD §18): "a solução mais simples" é cron do host chamando
  a CLI já existente (T7) — nenhuma lib nova, nenhum serviço novo. Vira
  T25 (script + doc, sem teste automatizado, mesmo padrão do T6).

---

## Execution Plan

### Phase 1 (sequential — mesma razão do M1/M2: adapters/api
integration compartilham estado/container Qdrant, TESTING.md marca
"Parallel-Safe: No")

```
T22  (independente)
T23  (independente)
T24  (independente)
T25  (independente)
```

Nenhuma task depende de outra nesta rodada — a ordem de execução é só
disciplina operacional (um sub-agente por vez), não dependência real de
código.

### Cross-check (diagram vs Depends on)

| Task | Diagram depends on | `Depends on` field |
|---|---|---|
| T22 | — | None |
| T23 | — | None |
| T24 | — | None |
| T25 | — | None |

### Test co-location check

| Task | Layer | Required test (TESTING.md) | Task's `Tests` field | OK? |
|---|---|---|---|---|
| T22 | api (source.py) | integration | integration | ✅ |
| T23 | adapters (wpp.py) + domain (messages.py) | integration + unit | unit + integration | ✅ |
| T24 | services (ingestion.py) | integration (precedente aceito em T13/T21 — orquestração real com Qdrant, mesma exceção já usada nas duas rodadas anteriores) | integration | ✅ |
| T25 | scripts (infra, sem camada de código) | none (mesmo padrão do T6 — compose/infra sem teste automatizado) | none | ✅ |

No task marked `[P]`: T22-T24 each touch an `adapters/api/services-
integration` test sharing the local Qdrant container — TESTING.md marks
that not parallel-safe. T25 has no test at all, so it's not worth
special-casing — kept in the same one-at-a-time execution discipline as
every prior milestone in this project.

---

## Task Breakdown

### T22: API — Source resolver (`GET /api/v1/source/{id}`, `GET /api/v1/source/media/{message_id}`)

**What**: `app/api/source.py` (new).
- `GET /api/v1/source/{context_id}`: reads the Qdrant point by
  `context_id` (the same derived UUID `POST /api/v1/search`'s
  `SearchResult.context_id` already returns, T14) via the existing
  `qdrant.py` client pattern. Returns `{chat: {id}, messages:
  [{message_id}], media: [{message_id, fetch_url}]}` — `messages` and
  `media` built straight from the payload's `source.message_ids` +
  `media[]` (T20). **SPEC_DEVIATION carried over from T12/T14**: the
  payload doesn't store per-message `sender`/`timestamp` yet, so those
  fields from PRD §12's example JSON are omitted here too, not
  fabricated — this endpoint returns exactly what's actually indexed.
  Unknown `context_id` → `404`, never `500`.
- `GET /api/v1/source/media/{message_id}`: delegates directly to
  `wpp.get_media(message_id)`, streams the file back with the real MIME
  type, deletes the downloaded temp file after the response is sent
  (FastAPI/Starlette `BackgroundTask` on the response — "sem cache
  permanente... cleanup garantido" per PRD §12). Missing/bad
  `message_id` (adapter raises `WppMediaNotFoundError`) → `404`.

**Where**: `app/api/source.py` (new), `app/main.py` (register router)
**Depends on**: None
**Reuses**: Qdrant client/point-id pattern from T12/T14, `wpp.get_media`
from T3 — no new adapter mechanism

**Done when**:
- [x] `GET /api/v1/source/{context_id}` for a previously-indexed fixture
      returns `chat.id` + the unit's `message_ids` + one `media` entry
      per artifact in the payload, each with a `fetch_url` pointing at
      `/api/v1/source/media/{message_id}`
- [x] `GET /api/v1/source/{context_id}` for an unknown id → `404`
- [x] `GET /api/v1/source/media/{message_id}` for a fixture with a real
      (synthetic) media message returns the actual bytes with the
      correct `Content-Type`
- [x] No temp file downloaded by this endpoint remains on disk after the
      response completes (test asserts on the actual path, not just
      "trust the background task")
- [x] `GET /api/v1/source/media/{message_id}` for a bad message_id →
      `404`, not `500`

**Tests**: integration (TestClient FastAPI + real local Qdrant with a
fixture already indexed via T12's upsert helper; `wpp.get_media` stubbed
with a small synthetic file for the media-fetch test)
**Gate**: full

**Gate da Fase 4**: resultado de busca leva à evidência original (PRD
§22 Cenário 8) — demonstrável via `POST /api/v1/search` (T15) →
`GET /api/v1/source/{id}` → `GET /api/v1/source/media/{message_id}`.

---

### T23: Hardening — limite de tamanho de mídia + validação de MIME

**What**: add `MEDIA_MAX_SIZE_MB` (default `25`, per PRD §21 "limites de
tamanho para mídia") to `Settings` (`app/config.py`) + document in
`.env.example`. Extend `app/adapters/wpp.py`'s `get_media()` to raise a
new `WppMediaTooLargeError(WppError)` when the size `wpp.sh` already
reports exceeds the configured limit — check the reported size, don't
re-`stat()` the file. Add a pure `validate_mime(declared_type: str,
mime: str) -> bool` helper (`app/domain/messages.py` — domain logic,
no I/O) checking the MIME prefix (`image/*`/`audio/*`/`video/*`) matches
the message's declared `type`; call it from `ingestion.py`'s
`_process_media()` right after `wpp.get_media()` returns, raising
(reusing the existing `WppError` hierarchy, or a plain `ValueError` —
either is already caught by T21's blanket `except Exception` →
`failed_media`, so **no new except branch needed in `ingestion.py`**.

**Where**: `app/config.py`, `.env.example`, `app/adapters/wpp.py`,
`app/domain/messages.py`, `app/services/ingestion.py` (call site only —
one line, calling `validate_mime()` after `get_media()`)
**Depends on**: None
**Reuses**: T21's `failed_media` isolation — new error types just need
to subclass `Exception`, already covered by the existing wrapper

**Done when**:
- [x] A media file over `MEDIA_MAX_SIZE_MB` raises
      `WppMediaTooLargeError` from `get_media()` (test with a small
      configured limit + a fixture file that exceeds it — no need to
      generate a real 25MB file, shrink the limit for the test)
- [x] A MIME/type mismatch (message declared `type="image"` but reported
      MIME is `audio/ogg`) is caught by `validate_mime()` and — wired
      through `ingestion.py` — marks that unit `failed_media`, not
      silently fed into `describe_image()`/`transcribe()`
- [x] No hardcoded limit anywhere in code — only `Settings`/`.env.example`
- [x] Existing T21 media fixtures (image/audio/video, within limits and
      correct MIME) still pass unmodified — no regression

**Tests**: unit (`validate_mime()` — pure function, table of
type/MIME pairs) + integration (`get_media()`'s size check +
`ingestion.py`'s wiring, real local Qdrant for the upsert step like
every other integration test here)
**Gate**: full

---

### T24: Hardening — idempotência de reingestão cobre unidades com mídia

**What**: extend the integration-test suite (do not touch production
code unless the test reveals a real gap) proving PRD §20's "Retry deve
ser simples e idempotente" already holds for media units, not just text
ones (T13 only proved it for text). Two scenarios:
1. Reingest the exact same period twice for a fixture with an
   image+audio+video unit → the Qdrant collection has exactly one point
   for that unit after both runs (not two), and its `media` payload is
   identical both times.
2. Simulate a `failed_media` on run 1 (e.g. `wpp.get_media` raises for
   one unit), then remove the simulated failure and reingest the same
   period again (run 2) → that same unit is now `processed`/indexed,
   with **no code change to `ingestion.py` required** — proving the
   existing idempotent-upsert design is itself the retry mechanism, no
   new retry/backoff code needed.

**Where**: `tests/integration/test_ingestion_media.py` (extend) or a new
`tests/integration/test_ingestion_retry.py` if that keeps the file
focused — sub-agent's call, matching this project's existing file-size
conventions
**Depends on**: None
**Reuses**: T21's media fixtures, T12's `point_id()`/`upsert()`
idempotency — this task is a test, not a new mechanism, unless the test
uncovers a real bug (in which case: fix it, and note the fix as a
SPEC_DEVIATION in the report, don't silently expand scope)

**Done when**:
- [x] Reingesting the same period twice with an all-media unit produces
      exactly one Qdrant point
- [x] A unit that was `failed_media` on run 1 is successfully indexed on
      run 2 after the simulated failure is removed
- [x] Existing T13/T21 tests still pass unmodified

**Tests**: integration
**Gate**: full

**Gate da Fase 5 (parcial — ver T25)**: idempotência/retry demonstrados
sem mecanismo novo (PRD §20).

---

### T25: Scheduler — wrapper de cron pra ingestão diária

**What**: `scripts/ingest-yesterday.sh` (new, executable) — computes
yesterday's ISO 8601 `start`/`end` (00:00 to 00:00, local timezone) and
calls the existing `wpp-context ingest --start <start> --end <end>`
(T7's CLI, unchanged). Document one crontab line example in `README.md`
(`03:00` per PRD §18's own example) pointing at this script. **No new
Python code, no scheduler library, no cron container** — PRD §18
explicitly forbids Celery/Redis and prefers "a solução mais simples
compatível com a infraestrutura atual"; a host-cron line calling the
already-tested CLI fully satisfies that.

**Where**: `scripts/ingest-yesterday.sh` (new), `README.md` (crontab
example)
**Depends on**: None
**Reuses**: T7's CLI `ingest` subcommand — zero changes to it

**Done when**:
- [x] Running `./scripts/ingest-yesterday.sh` manually (against a short
      test window, e.g. overriding the date via an env var or arg if
      that's simpler than mocking `date`) invokes the CLI with the
      correct computed start/end and exits `0`
- [x] `README.md` documents the crontab line

**Tests**: none (shell wrapper around an already-tested CLI command —
manual smoke only, same as T6's docker-compose task)
**Gate**: none

**Gate da M3**: PRD §22 Cenário 7 (reprocessamento) e Cenário 8
(recuperação da origem) demonstráveis; hardening items from §21 that
had real gaps (media size/MIME) closed; scheduler documented — critério
de saída do M3.

---

## Próxima rodada (fora deste tasks.md)

- Nenhuma prevista pelo PRD além do que já está em "Evoluções
  possíveis" (§27) — não faz parte do MVP, não vira milestone sem pedido
  explícito do usuário.
