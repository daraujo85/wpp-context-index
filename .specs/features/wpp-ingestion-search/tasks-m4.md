# WPP Ingestion & Search Tasks — M4 (Mensagens na íntegra)

**Spec**: `.specs/features/wpp-ingestion-search/spec.md` (P4: Mensagens
na íntegra, ligadas ao resumo)
**Design**: `.specs/features/wpp-ingestion-search/design.md` (§ "Camada
de mensagens")
**Depends on**: M3 done (T22-T25)
**Status**: T26-T32 done. Backfill real 2026-08-01..2026-10-01: 24/29 fontes
concluídas (~9.3k msgs); restantes retomam rodando o mesmo comando (idempotente).
Mídia de unidade sem resumo não é baixada (gateway não guarda mídia antiga):
fica tag + legenda + `media_ref`.

**Escopo**: segunda camada no Qdrant — collection `wpp_messages`, um
ponto por mensagem, **sempre texto normalizado** (texto redigido, legenda,
descrição de imagem, transcrição de áudio/vídeo), ligada ao resumo
(`wpp_context`) por `context_id`, com ponteiro pra mídia original. Resumo
continua sendo a porta de entrada da busca (palavra solta no texto cru
gera match ruim); mensagens são complemento acessível pelo resumo ou
buscáveis direto.

**Decisão nova (desvio do PRD, pedido explícito do usuário 2026-10-01)**:
PRD §8 / design "descartada = nada persiste" passa a valer só pra
`wpp_context`. Em `wpp_messages` **toda mensagem que passa no pré-filtro
determinístico é persistida** — inclusive as de unidades que o Guard Rail
descartou (`context_id = null`). Redaction de segredos continua
obrigatória no texto. Mídia binária continua **não** armazenada (só texto
+ `media_ref`).

**Discovery (lida no código/gateway real, 2026-10-01)**:

- `wpp.sh history` tinha sumido de novo (arquivo reescrito em 23/09 com
  `cmd_messages`/`cmd_polls`). Restaurado + comentário "não remover" no
  dispatch. Fora do repo (skill), sem teste aqui — o adapter `wpp.history`
  foi validado ao vivo (35 msgs rodrigo 29/09).
- **Retenção do gateway**: `getAllMessagesByContactId` devolve 0 pra
  abr–jul em qualquer chat testado; ago/set completos. `wpp-inbound.jsonl`
  local só cobre 05–09/06. Backfill de `wpp_messages` fica limitado a
  ago–set — gap aceito, não contornável.
- Mídia: o texto do usuário vem em `caption` (`body` = `""` ou thumbnail
  base64) — `normalize()` hoje perde legenda de imagem/documento. Grupo:
  remetente em `author` (`@lid`) + `sender` dict (`pushname`/`name`) —
  hoje `sender_id` recebe o dict. Tipos crus não mapeados: `album`
  (container, as imagens chegam como msgs próprias), `ciphertext`.
- `_process_media` descarta a transcrição de vídeo (só usa frames).
- Nenhum código cria `wpp_context` (foi criada manualmente) — a nova
  collection precisa de criação lazy + índices de payload.
- Qdrant 1.19.1 / qdrant-client com `TextIndexParams` — full-text nativo
  em `text`, sem dependência nova.

---

## Execution Plan

Sequencial (mesmo motivo de M1-M3: integration compartilha Qdrant local).

```
T26 → T27 → T28 → T29 → T30 → T31 → T32
```

---

## Task Breakdown

### T26: Normalização — remetente, legenda, tipos novos

**What**: `normalize()` — `sender_id` = `author` em grupo, senão
`sender._serialized`/`sender` str; `sender_name` = `sender.pushname` /
`name`; `body` de mídia = `caption` quando `body` vazio/base64;
`filename` preservado; `t` coerced a `int`; `album`/`ciphertext` →
`system`.
**Where**: `app/domain/messages.py`
**Tests**: unit
**Done when**: testes antigos passam; novos cobrem grupo/author,
caption de imagem, album descartado.

### T27: Domínio de mensagem — texto normalizado + janela de embedding

**What**: `app/domain/message_text.py` (puro): `message_text(msg,
media_text)` → string única (texto redigido; `[imagem] desc`;
`[audio] transcrição`; `[video] transcrição + frames`; `[documento:
nome] legenda`); `embedding_window(texts, i, radius=1)` → msg + vizinhas
da mesma unidade.
**Tests**: unit

### T28: Adapter Qdrant — collection `wpp_messages`

**What**: `message_point_id(message_id)` (uuid5 determinístico),
`ensure_messages_collection()` (lazy, cria com o tamanho do 1º vetor +
índice full-text em `text`, keyword em `context_id`/`chat.id`/`day`),
`upsert_messages(points, ...)`.
**Where**: `app/adapters/qdrant.py`
**Tests**: integration (Qdrant real, collection de teste)

### T29: Ingestão grava mensagens

**What**: `run()` — após Guard Rail, toda unidade (index **ou** discard)
grava suas mensagens em `wpp_messages` com `context_id` (ou `null`).
Falha de mídia/guard rail → nada gravado (unidade será reprocessada,
fonte não fica `done`). `_process_media` passa a devolver texto por
mensagem (inclui transcrição de vídeo). Novo contador
`messages_indexed`. Collection de mensagens derivada do nome da de
contextos (testes nunca tocam `wpp_messages` real).
**Where**: `app/services/ingestion.py`
**Tests**: integration

### T30: Backfill de mensagens sem LLM de texto

**What**: `backfill_messages(start, end)` — history → normalize →
pré-filtro → group (determinístico ⇒ mesmos `unit.id`) → busca o
`context_id` existente em `wpp_context`; mídia de unidade indexada reusa
`payload.media` (zero chamada de visão/Whisper); só mídia de unidade
descartada é reprocessada. Estado próprio (`("messages:"+alias, start,
end)`). CLI `backfill-messages --start --end`.
**Where**: `app/services/ingestion.py`, `app/cli/__main__.py`
**Tests**: integration

### T31: Busca em duas camadas

**What**: `search(..., scope="contexts"|"messages", expand=False)`.
`contexts` (default, inalterado) + `expand` anexa mensagens originais
(ordenadas por timestamp). `messages`: busca densa em `wpp_messages`
(+ boost via full-text quando há identificador), cada hit traz o resumo
do seu contexto. `/api/v1/search` aceita `scope`/`expand`;
`/api/v1/source/{id}` devolve mensagens com texto/remetente/timestamp
(fecha SPEC_DEVIATION do T22). CLI `search --scope --expand`.
**Where**: `app/services/search.py`, `app/api/search.py`,
`app/api/source.py`, `app/cli/__main__.py`
**Tests**: integration

### T32: Skill + docs + backfill real

**What**: `search.sh --messages/--expand`, `SKILL.md`, README (fluxo +
uso), ROADMAP M4; rodar backfill ago–set de verdade.
**Tests**: none (docs/infra) — smoke manual
