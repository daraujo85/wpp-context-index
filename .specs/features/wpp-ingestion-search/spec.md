# WPP Ingestion & Search Specification

## Problem Statement

Conhecimento operacional do time Prata (bugs, decisões, mockups, links,
regras de negócio) fica preso em conversas de WhatsApp, sem busca
semântica. Reler dias de histórico pra achar uma evidência é caro. É
preciso indexar apenas o que é profissionalmente relevante, com
rastreabilidade até a mensagem original, sem depender de LLM externa nem
guardar cópia permanente de mídia.

## Goals

- [ ] Ingestão em batch, idempotente, sobre as fontes já allowlisted em
      `whatsapp.env`/`whatsapp-lids.json`
- [ ] Busca semântica sem LLM generativa, com evidência (grupo/DM, autor,
      data, tipo, IDs, score, referência pra recuperação)
- [ ] Guard Rail de privacidade: conteúdo pessoal nunca gera embedding
      nem persiste

## Out of Scope

| Feature | Reason |
|---|---|
| UI web / chatbot / WhatsApp em tempo real | PRD §26 — fora do MVP |
| PostgreSQL / Elasticsearch / Redis / Kafka / Celery | PRD §2.6/§18 — Qdrant + scheduler simples bastam |
| LLM/API de IA externa no pipeline | PRD §2.1 — local-first obrigatório |
| Armazenamento permanente de mídia | PRD §2.5 |
| Ingestão automática na Wiki | PRD §16 — bases separadas no MVP |
| `sources.yaml` próprio | Decisão desta sessão — reusa `whatsapp.env`/`whatsapp-lids.json` existentes |
| Qdrant central compartilhado | Decisão desta sessão — Qdrant dedicado local no compose |

---

## User Stories

### P1: Busca semântica de texto ⭐ MVP

**User Story**: Como membro do time Prata, quero perguntar em linguagem
natural sobre algo discutido em texto no WhatsApp e receber a evidência
(grupo, autor, data, trecho, referência da mensagem).

**Why P1**: É o caminho mais simples que já entrega valor fim-a-fim
(PRD Fase 2, gate: busca semântica de texto funcionando).

**Acceptance Criteria** (PRD §22, Cenário 1):

1. WHEN uma mensagem de texto relevante é ingerida num chat allowlisted
   THEN o sistema SHALL indexar uma unidade de contexto com título,
   resumo, chat, autor(es), timestamps e `message_ids` de origem.
2. WHEN o usuário busca por um conceito relacionado (não palavra exata)
   THEN o sistema SHALL retornar a unidade com score, sem chamar LLM
   generativa.
3. WHEN o mesmo intervalo é reingerido THEN o sistema SHALL fazer upsert
   pelo ID determinístico, sem duplicar o ponto no Qdrant.
4. WHEN um chat allowlisted não tem mensagem nova no intervalo pedido
   THEN o sistema SHALL não escrever nada (nem placeholder) — só contar
   `messages_read: 0` no resumo agregado da execução.

**Independent Test**: ingerir fixture sintética com 1 mensagem de texto
relevante + buscar por paráfrase → resultado com evidência completa.
Reingerir o mesmo período → contagem de pontos no Qdrant não muda.

---

### P1: Guard Rail de privacidade ⭐ MVP

**User Story**: Como responsável pelo produto, quero garantir que
conversa pessoal (saúde, família, assuntos íntimos) nunca vire embedding
nem payload persistido, mesmo vindo de uma DM allowlisted.

**Why P1**: É requisito de privacidade por design (PRD §2.4), bloqueante
pra qualquer ingestão real — sem isso o MVP não pode rodar sobre dados
reais do time.

**Acceptance Criteria** (PRD §22, Cenários 5 e 6):

1. WHEN uma unidade de contexto é classificada como `discard` pelo Guard
   Rail THEN o sistema SHALL não gerar embedding, não persistir no
   Qdrant e não deixar resumo em arquivo temporário.
2. WHEN uma unidade mistura conteúdo pessoal e profissional THEN o
   sistema SHALL extrair e indexar somente a parte profissional
   sanitizada, nunca usando o trecho pessoal no texto do embedding.
3. WHEN uma mensagem contém padrão de segredo (token/senha/OTP/chave de
   API) THEN o sistema SHALL redigir ou descartar a unidade, preferindo
   descartar em caso de dúvida.

**Independent Test**: fixture com DM mista (trecho pessoal + decisão
técnica) → resultado indexado contém só a parte técnica; busca por termo
do trecho pessoal não retorna nada.

---

### P2: Busca multimodal (imagem/áudio/vídeo)

**User Story**: Como membro do time, quero achar uma evidência mesmo
sem saber que ela era uma imagem, áudio ou vídeo — buscando pelo
conteúdo/erro descrito.

**Why P2**: Amplia cobertura pra fora de texto puro, mas não bloqueia o
valor inicial da busca de texto (PRD Fase 3).

**Acceptance Criteria** (PRD §22, Cenários 2, 3, 4):

1. WHEN uma imagem é processada THEN o sistema SHALL gerar uma descrição
   objetiva focada em recuperação (texto visível, erro, tela, botão) via
   Ollama vision local (`gemma4:12b`), sem usar `azap-image`/Gemini.
2. WHEN um áudio ou vídeo é processado THEN o sistema SHALL reusar a
   skill `transcribe-audio-video` (subprocess) pra transcrição e, no
   caso de vídeo, frames-chave — sem reimplementar Whisper/ffmpeg.
3. WHEN a mídia termina de ser processada THEN o sistema SHALL apagar o
   arquivo temporário (bloco `finally`), mantendo só descrição/transcrição
   resumida aprovada pelo Guard Rail + metadados.

**Independent Test**: fixture com imagem simulada (erro de tela) e áudio
mock → busca pelo conteúdo descrito/transcrito encontra a unidade; nenhum
arquivo de mídia sobra em `/tmp` após o run.

---

### P3: Recuperação da origem & consumo por agente

**User Story**: Como agente externo (ex: Pratinha) ou usuário, quero
partir de um resultado de busca e chegar à mensagem/mídia original no
WPP Bot Server, sem conhecer Qdrant/embeddings.

**Why P3**: Fecha o ciclo de rastreabilidade e o caso de uso "agente
consome só a API" (PRD §22 Cenário 8-9, §15), mas depende dos P1/P2
existirem primeiro.

**Acceptance Criteria**:

1. WHEN o cliente chama `GET /api/v1/source/{context_id}` THEN o sistema
   SHALL devolver chat, mensagens de origem e referências de mídia
   (`fetch_url`), sem cache permanente.
2. WHEN o cliente chama `GET /api/v1/source/media/{message_id}` THEN o
   sistema SHALL buscar a mídia sob demanda no WPP Bot Server (via
   `wpp.sh get-media`) e devolvê-la, com cleanup garantido de qualquer
   temporário.
3. WHEN um agente chama `POST /api/v1/search` THEN o sistema SHALL
   devolver JSON determinístico e limpo, sem exigir conhecimento de
   Qdrant/embeddings/WPP.

**Independent Test**: a partir de um resultado de busca, chamar
`/source/{id}` e `/source/media/{message_id}` e recuperar o conteúdo
original correspondente.

---

## Traceability

| Story | PRD ref | Milestone (ROADMAP.md) |
|---|---|---|
| Busca semântica de texto | §6, §9, §10, §11, §22 Cenário 1/7 | M1 |
| Guard Rail de privacidade | §8, §22 Cenário 5/6 | M1 |
| Busca multimodal | §6.3, §22 Cenário 2/3/4 | M2 |
| Recuperação da origem & agente | §12, §15, §22 Cenário 8/9 | M3 |
