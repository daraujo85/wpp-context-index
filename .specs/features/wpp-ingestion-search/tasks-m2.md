# WPP Ingestion & Search Tasks — M2 (Multimodal)

**Design**: `.specs/features/wpp-ingestion-search/design.md`
**Spec**: `.specs/features/wpp-ingestion-search/spec.md` (P2: Busca multimodal)
**Depends on**: M1 done (T1-T15, commits `8ce705c`..`94adaeb`)
**Status**: T16-T21 done (M2 code-complete)

**Escopo desta rodada**: PRD Fase 3 — imagem/áudio/vídeo pesquisáveis
(spec.md P2, PRD §22 Cenários 2/3/4). Continua a numeração de tasks a
partir de T16 (T1-T15 já feitas em M1) pra manter rastreabilidade num só
histórico de tasks da feature.

**Tools por task**: skill `transcribe-audio-video`
(`~/.claude/skills/transcribe-audio-video/transcribe_audio.py`, subprocess)
entra em escopo agora. `whatsapp-message` (`wpp.sh get-media`, já
implementado em T3) é reusado sem mudança. Nenhuma MCP necessária.

**Discovery já feita** (ao escrever este tasks.md, lendo
`transcribe_audio.py` real em vez de assumir):

- CLI: `python3 transcribe_audio.py <arquivo> --format json [--language pt]
  [--frames N --frames-out DIR]`. Sem `--frames`: só transcrição de áudio.
  Com `--frames`: extrai frames-chave via ffmpeg pra `--frames-out DIR`
  (se omitido, cria `/tmp/transcribe-frames-<slug>/` sozinho — **sempre
  passar `--frames-out` explícito** pra controlar e apagar o dir depois).
- JSON de sucesso: `{success, file, model, language, duration, text,
  segments: [...], segment_count}` + (com `--frames`) `key_frames: [{t,
  when, frame: <path.jpg>, cue, dims, elements, actions}], frames_dir}`.
- Frames **não vêm descritos** pela skill — ela deixa a leitura/descrição
  pro agente (mesmo padrão já confirmado pra imagem em M0: sem
  capacidade de descrição pronta, decisão já tomada de usar Ollama vision
  direto). Cada `key_frames[i].frame` é só um JPEG — precisa do adapter
  de visão (T16) pra virar texto.
- Falha sem `--frames`: stderr + `exit(1)`, nada no stdout — subprocess
  adapter deve tratar `returncode != 0` como erro tipado, não tentar
  `json.loads` de saída vazia.
- Cache próprio da skill em `~/.cache/claude-audio-transcriptions/`
  (fora do nosso controle, não é "mídia armazenada permanentemente" pelo
  nosso pipeline — é cache da skill compartilhada, aceito como está,
  fora de escopo mexer nisso).

---

## Execution Plan

### Phase 1 (sequential — adapters/* integration tests não são
parallel-safe per TESTING.md, compartilham estado/containers)

```
T16 → T18 → T19
T17 ↗
T20 ───────────→ T21
(T16, T17, T18, T19, T20 todos alimentam T21)
```

### Cross-check (diagram vs Depends on)

| Task | Diagram depends on | `Depends on` field |
|---|---|---|
| T16 | — | None |
| T17 | — | None |
| T18 | T16, T17 | T16, T17 |
| T19 | T18 | T18 |
| T20 | — | None |
| T21 | T16, T17, T18, T19, T20 | T16, T17, T18, T19, T20 |

### Test co-location check

| Task | Layer | Required test (TESTING.md) | Task's `Tests` field | OK? |
|---|---|---|---|---|
| T16 | adapters (inference.py) | integration | integration | ✅ |
| T17 | adapters (transcription.py, new) | integration | integration | ✅ |
| T18 | domain (guardrail.py) + adapters (inference.py) | unit + integration | unit + integration | ✅ |
| T19 | services (embedding.py) | unit | unit | ✅ |
| T20 | adapters (qdrant.py) | integration | integration | ✅ |
| T21 | services (ingestion.py) | integration | integration | ✅ |

No task marked `[P]`: every task here touches an `adapters/*` integration
test, and TESTING.md marks that layer **not parallel-safe** ("compartilham
container Qdrant/estado") — so even the code-independent tasks (T16, T17,
T20) run one sub-agent at a time, not concurrently.

---

## Task Breakdown

### T16: Adapter — descrição de imagem via Ollama vision

**What**: extend `app/adapters/inference.py` with `describe_image(image_path,
timeout_seconds=120) -> str` — HTTP POST to `{OLLAMA_BASE_URL}/api/generate`
with `model=Settings().vision_model`, the image base64-encoded in the
`images` field, and a prompt asking for an objective, retrieval-oriented
description (per PRD §6.3 example: texto visível, erro, tela/sistema,
botão/ação — never "artistic" description). Raises `InferenceError` (same
exception T11 already defined) on HTTP/network failure or empty response.
**Where**: `app/adapters/inference.py`
**Depends on**: None (T11/T12 already built the module's HTTP pattern;
`vision_model` field already exists on `Settings` since T1)
**Reuses**: `InferenceError`, `Settings().vision_model`, the
`urllib.request` HTTP pattern already used by `classify_and_extract`/`embed`

**Done when**:
- [x] `describe_image()` returns a non-empty string for a real local test
      image (synthetic — e.g. a screenshot-like PNG with visible text/an
      error message rendered onto it, generated in the test, not a real
      user screenshot)
- [x] Raises `InferenceError` if Ollama is unreachable or returns non-200
- [x] Uses the same `OLLAMA_BASE_URL` override pattern as T11 — remember
      the shell's ambient `OLLAMA_BASE_URL` is wrong
      (`http://localhost:11434/v1`); the real value is
      `http://192.168.31.231:11434`. `Settings` already reads env
      correctly; only matters if you curl Ollama directly while testing.

**Tests**: integration (real Ollama vision call — confirm `VISION_MODEL`
default from `.env.example`, e.g. `gemma4:12b`, is actually pulled on the
server before assuming it'll respond; if not, use whatever vision model
IS present and note it as SPEC_DEVIATION rather than blocking)
**Gate**: full

---

### T17: Adapter — transcrição de áudio/vídeo via `transcribe-audio-video`

**What**: `app/adapters/transcription.py` (new) — subprocess wrapper:
`transcribe(media_path, media_type, frames_out=None, timeout_seconds=300)
-> TranscriptionResult`. For `media_type="audio"`: runs `transcribe_audio.py
<media_path> --format json --language pt`. For `media_type="video"`: same
+ `--frames 3 --frames-out <frames_out or a tempfile.mkdtemp()>` (cap of 3
frames — MVP, keep vision calls cheap; don't make this configurable unless
a real need shows up). Parses stdout JSON. `TranscriptionResult` dataclass:
`text`, `frame_paths: list[str]` (empty for audio), `frames_dir:
str | None` (so the caller can delete it).
**Where**: `app/adapters/transcription.py`
**Depends on**: None
**Reuses**: nothing from other adapters — mirrors T3's subprocess
error-handling shape (typed `TranscriptionError`, not a bare exception)

**Done when**:
- [x] Real smoke test: generate a tiny synthetic audio file (e.g. via
      `ffmpeg -f lavfi -i anullsrc -t 1 test.wav` or similar silent/short
      clip — no real conversation audio) and a tiny synthetic video file,
      confirm the subprocess actually runs and `transcribe()` returns a
      `TranscriptionResult` without crashing (empty/near-empty transcript
      is fine — the point is the subprocess contract works, not
      transcription quality)
- [x] `media_type="video"` populates `frame_paths` with real file paths
      that exist on disk at return time (caller decides when to delete)
- [x] Non-zero exit code / malformed stdout → `TranscriptionError`, never
      an unhandled exception
- [x] Whisper/openai-whisper or ffmpeg missing in this environment is a
      real possible outcome — if so, document exactly what failed as
      SPEC_DEVIATION (don't silently skip the test)

**Tests**: integration
**Gate**: full

---

### T18: Domain — Guard Rail schema estendido com `artifacts`

**What**: extend `app/domain/guardrail.py`'s `GuardrailResult` with
`artifacts: list[dict]` (each `{type, description}`, per PRD §9's example
JSON) and `parse_guardrail_response()` to accept/default it (`[]` when
absent or malformed — never crash the batch). Extend
`classify_and_extract()`'s prompt so that when the unit text passed in
includes an image description or a transcript, the model is asked to also
return `artifacts` describing that media (still ONE LLM call — no new
call added).
**Where**: `app/domain/guardrail.py`, `app/adapters/inference.py`
(prompt text only)
**Depends on**: T16, T17 (need real description/transcript text shapes to
write a sane prompt example)
**Reuses**: `GuardrailResult`, `parse_guardrail_response()`,
`classify_and_extract()` from T11 — extend, don't duplicate

**Done when**:
- [x] Unit test: LLM response JSON with a valid `artifacts` list parses
      into `GuardrailResult.artifacts` unchanged
- [x] Unit test: LLM response missing `artifacts` → `[]`, doesn't crash
      (same fallback discipline as the other 8 fields from T11)
- [x] Integration test: real Ollama call with unit text that includes a
      fake "[imagem] tela mostrando erro 500..." line → response's
      `artifacts` is inspected (best-effort — a local 8B model may not
      always populate it correctly; assert it at least parses without
      crashing, don't assert exact content)

**Tests**: unit (parser) + integration (real Ollama call)
**Gate**: full

---

### T19: Services — texto de embedding inclui artifacts

**What**: extend `app/services/embedding.py`'s `build_embedding_text()` to
append artifact descriptions (`"; ".join(a["description"] for a in
guardrail.artifacts)`) after topics — closes the gap T12 explicitly left
open ("skipped rather than inventing one... add artifacts text if T11's
schema is later extended" — it just was, by T18).
**Where**: `app/services/embedding.py`
**Depends on**: T18
**Reuses**: `build_embedding_text()` from T12 — extend, don't rewrite

**Done when**:
- [x] `GuardrailResult` with a non-empty `artifacts` list produces
      embedding text that contains each artifact's `description`
      substring
- [x] `GuardrailResult` with `artifacts=[]` produces the exact same output
      as before this task (no regression on T12's 3 existing unit tests)

**Tests**: unit
**Gate**: quick

---

### T20: Adapter — payload Qdrant inclui `media`

**What**: extend `app/adapters/qdrant.py`'s `build_payload()` with an
optional `media: list[dict] | None = None` param (each `{message_id,
type, description}`, per PRD §10.2's `media[]` field) — included in the
payload dict only when non-empty/non-None, so text-only units (M1) keep
their exact current payload shape.
**Where**: `app/adapters/qdrant.py`
**Depends on**: None (additive param, independent of T16-T19's actual
call sites — T21 is what supplies real data)
**Reuses**: `build_payload()` from T12 — extend, don't rewrite

**Done when**:
- [x] `build_payload(..., media=[{"message_id": "x", "type": "image",
      "description": "..."}])` includes that `media` list verbatim in the
      returned payload
- [x] `build_payload(...)` with no `media` arg produces byte-identical
      output to before this task (existing T12 integration tests for
      text-only units still pass unmodified)

**Tests**: integration
**Gate**: full

---

### T21: Services — ingestion processa mídia fim-a-fim [Gate M2]

**What**: extend `app/services/ingestion.py`'s `run()` to, per
`ContextUnit`, detect which of its original messages have `has_media=True`
and dispatch by `type`:
- `image` → `wpp.get_media(message_id)` → `inference.describe_image()` →
  one artifact `{type: "image", description}`
- `audio` → `wpp.get_media(message_id)` → `transcription.transcribe(...,
  media_type="audio")` → one artifact `{type: "audio", description:
  <text>}`
- `video` → `wpp.get_media(message_id)` → `transcription.transcribe(...,
  media_type="video")` → `inference.describe_image()` on each returned
  frame path → one artifact per described frame, `type: "video"`
- `document` → **no download, no adapter call** — PRD §6.3 says index
  only caption/filename/type/links for documents in the MVP; the
  message's existing `body` (caption) already flows into `unit_text` as-
  is, nothing new to build here (no `filename` field exists on
  `MessageEnvelope` — noted as a known gap, not fixed this round)

Append artifact descriptions to the `unit_text` fed into
`classify_and_extract()` (so T18's schema can populate `artifacts`), then
after a successful `index` decision, build the `media` list (T20's new
param) from the same artifacts + their source `message_id`/`type` and
pass it into `build_payload()`.

**Media download/describe/transcribe MUST be wrapped in `try/finally`**:
on any adapter error (`WppMediaNotFoundError`, `InferenceError`,
`TranscriptionError`), mark that unit `failed_media` (new status, per PRD
§20's state list — T13 didn't need it since M1 was text-only), count it
in `errors`, and move to the next unit — same per-unit isolation
discipline as T13's `failed_guardrail`/`failed_embedding`. `finally` must
delete the downloaded media file AND (for video) the frames directory,
regardless of success/failure — no temp file/frame ever survives past
the unit's processing, even on the happy path.

Processing stays strictly sequential (one media item at a time, no
threading) — this already satisfies the default
`MEDIA_CONCURRENCY=1`/`INGEST_CONCURRENCY=1` from PRD §17.2; don't add a
concurrency primitive nobody's using yet.

**Where**: `app/services/ingestion.py` (extend `run()`; the existing
`_unit_text()` helper from T13 gets a media-artifacts append step)
**Depends on**: T16, T17, T18, T19, T20 (needs every M2 piece wired)
**Reuses**: everything from T13 plus T16-T20

**Done when**:
- [x] Fixture with a simulated image message (screenshot-like PNG with
      visible error text) → indexed unit's Qdrant payload has a `media`
      entry and its embedding text contains the vision description
- [x] Fixture with a mock audio message → indexed unit's embedding text
      contains (a substring of) the transcript
- [x] Fixture with a mock video message → indexed unit's embedding text
      contains at least one frame description
- [x] A media adapter failure (simulate by pointing `wpp.get_media` at a
      bad message id, or monkeypatching the adapter to raise) marks that
      unit `failed_media`, counts in `errors`, and the batch still
      finishes processing the remaining units (test asserts this
      explicitly, mirroring T13's `failed_guardrail` isolation test)
- [x] After the full test run, no file remains under the temp
      dirs/paths this task created (assert on the actual tmp path(s)
      used, not just "trust the finally block")
- [x] Document-type message in the same batch is NOT sent to
      `wpp.get_media`/`transcription.transcribe`/`inference.describe_image`
      (assert those mocks are not called for it)
- [x] Existing T13 text/personal/mixed/empty-chat fixtures still pass
      unmodified (no regression)

**Tests**: integration (fixtures: imagem simulada, áudio mock, vídeo
mock, reusing T13's texto/pessoal/misto/vazio fixtures for regression;
`wpp`/`inference`/`transcription` stubbed with synthetic data, real local
Qdrant for the upsert step — same pattern T13 already established)
**Gate**: full

**Gate da M2**: PRD §22 Cenários 2/3/4 (imagem, áudio, vídeo)
demonstráveis via `services.search.search()` (T14, unchanged — it already
searches whatever text ended up in the embedding, so once T21 lands,
multimodal content becomes findable with zero changes to `search.py` or
the API layer) — critério de saída do M2 (multimodal).

---

## Próxima rodada (fora deste tasks.md)

- **M3 (source resolver + hardening)**: `tasks.md` novo depois que T21
  estiver validado — `api/source.py`, retry, testes de carga leve,
  scheduler (cron). Nesta rodada também ficam pendentes (deliberadamente
  fora de M2): `urls`/`ticket_ids`/`senders`/timestamps no payload
  (T12/T14's SPEC_DEVIATION gaps), indexação de nome de arquivo pra
  `document` — nenhum bloqueia o gate de M2.
