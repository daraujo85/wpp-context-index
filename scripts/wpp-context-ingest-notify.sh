#!/usr/bin/env bash
# Cron-safe wrapper around wpp-context-index's ingest-yesterday.sh: runs
# outside ~/Documents (TCC blocks cron/launchd execing scripts there) and
# notifies 21995278672 on WhatsApp with the result, so a missing message
# means the routine didn't run. Template approved once by the user for
# every future run — no per-message confirmation needed.
#
# CANONICAL COPY: this file (versioned in the repo). ~/.local/bin/wpp-context-
# ingest-notify.sh is a one-line shim that execs this script, so the logic
# the cron entry (crontab: 0 3 * * *) actually runs is reviewable in git.
#
# Message states the actual ingested date range (not send-time) and a
# managerial breakdown (group/contact: N msg) parsed from the CLI's own
# "total por contato (só esta execução)" block — per user feedback
# 2026-11-04 that send-time timestamps and bare totals were uninformative.
#
# Test hooks (used by tests/integration/test_notify_script.py):
#   DRY_RUN=1          -> print the message instead of sending it
#   WPP_SEND_SCRIPT    -> override the sender (default: whatsapp-message/wpp.sh)
#   WPP_SYNC_SCRIPT    -> override/"" the pre-ingest allowlist sync
set -u

export PATH="/opt/homebrew/bin:/Users/diegoaraujo/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJECT_DIR="/Users/diegoaraujo/projects/wpp-context-index"
WPP="${WPP_SEND_SCRIPT:-/Users/diegoaraujo/.claude/skills/whatsapp-message/wpp.sh}"
SYNC_SCRIPT="${WPP_SYNC_SCRIPT-/Users/diegoaraujo/.local/bin/wpp-prata-sync.sh}"
LOG="${WPP_INGEST_LOG:-/Users/diegoaraujo/Library/Logs/wpp-context-ingest.log}"
TO="5521995278672"

# Catch-up window: the CLI runs ONE DAY PER DAY inside the window (the
# ingestion state is keyed by alias+day), so a night that missed a day —
# dead WhatsApp session, crash, host asleep — leaves that day unmarked and
# the next night reprocesses exactly what failed while the days already
# done are skipped without a single API call. 7 days covers a weekend of
# failures without re-paying the LLM for weeks of history.
LOOKBACK_DAYS="${INGEST_LOOKBACK_DAYS:-7}"

# Resolve the same yesterday..today range ingest-yesterday.sh would compute,
# so we can state it in the message even if INGEST_START/END aren't set.
if date -v-1d +%F >/dev/null 2>&1; then
  YESTERDAY_CMD=(date -v-1d +%F)   # BSD date (macOS)
  DAY_BEFORE_CMD=(date -v-${LOOKBACK_DAYS}d +%F)
else
  YESTERDAY_CMD=(date -d yesterday +%F)  # GNU date (Linux)
  DAY_BEFORE_CMD=(date -d "${LOOKBACK_DAYS} days ago" +%F)
fi
START="${INGEST_START:-$("${DAY_BEFORE_CMD[@]}")}"
END="${INGEST_END:-$(date +%F)}"
export INGEST_START="$START" INGEST_END="$END"

if [ -n "$SYNC_SCRIPT" ] && [ -x "$SYNC_SCRIPT" ]; then
  "$SYNC_SCRIPT" >> "$LOG" 2>&1 || true
fi

OUT="$("$PROJECT_DIR/scripts/ingest-yesterday.sh" 2>&1)"
STATUS=$?

if [ $STATUS -eq 0 ]; then
  # The CLI prints its machine-readable summary AFTER the Rich Live panel.
  # Rich's panel render does NOT end with a newline when stdout isn't a TTY
  # (cron pipes it), so the summary used to land glued to the panel's closing
  # border ("╯ingest: range ...") and this anchored grep never matched —
  # every daily report said "0 msgs lidas · 0 indexadas" even on runs that
  # read 268 msgs and indexed 53 units. Match anywhere in the line, and treat
  # "no match" as a FAILED report (never as "nothing happened").
  LINE="$(printf '%s\n' "$OUT" | grep -m1 -oE 'ingest: range .*')"
  READ_N="$(printf '%s' "$LINE" | grep -oE 'messages_read=[0-9]+' | cut -d= -f2)"
  IDX_N="$(printf '%s' "$LINE" | grep -oE 'contexts_indexed=[0-9]+' | cut -d= -f2)"
  ERR_N="$(printf '%s' "$LINE" | grep -oE 'errors=[0-9]+' | cut -d= -f2)"

  # Managerial breakdown: "  alias (grupo|dm): N msg" lines after the
  # "total por contato (só esta execução):" header. Only sources with
  # activity THIS run (0-msg sources are either genuinely quiet or already
  # ingested earlier — the CLI's own idempotent skip, not re-counted here).
  # WhatsApp markdown: *bold*, bullets, blank lines between sections —
  # per user feedback 2026-11-04 ("formata melhor, com lista, nebrito,
  # emoji, linha em branco").
  # Ordenado por maior volume primeiro dentro de cada lista (grupos/contatos)
  # — pedido do Diego 2026-11-04.
  BREAKDOWN="$(printf '%s\n' "$OUT" | awk -F'[()]' '
    /^  .+ \((grupo|dm)\): [0-9]+ msg$/ {
      split($0, a, ":"); n=a[2]; gsub(/[^0-9]/, "", n);
      if (n+0 > 0) {
        alias=$1; gsub(/^  /, "", alias); gsub(/ $/, "", alias)
        if ($2 == "grupo") { gn++; gname[gn]=toupper(alias); gcnt[gn]=n+0; gtot+=n+0 }
        else { cn++; cname[cn]=toupper(alias); ccnt[cn]=n+0; ctot+=n+0 }
      }
    }
    function sortd(name, cnt, n,    i, j, tn, tc) {
      for (i=1; i<=n; i++) for (j=i+1; j<=n; j++)
        if (cnt[j] > cnt[i]) { tc=cnt[i]; cnt[i]=cnt[j]; cnt[j]=tc; tn=name[i]; name[i]=name[j]; name[j]=tn }
    }
    END {
      sortd(gname, gcnt, gn); sortd(cname, ccnt, cn)
      if (gn) { print ""; print "📢 *Grupos* (" gn " · " gtot " msg)"; for (i=1;i<=gn;i++) print "• *" gname[i] "*: " gcnt[i] " msg" }
      if (cn) { print ""; print "👤 *Contatos* (" cn " · " ctot " msg)"; for (i=1;i<=cn;i++) print "• *" cname[i] "*: " ccnt[i] " msg" }
    }
  ')"

  if [ -z "$BREAKDOWN" ]; then
    NOTE=$'\n\n'"ℹ️ Sem atividade nova — dia já processado ou genuinamente parado."
  else
    NOTE="$BREAKDOWN"
  fi

  # Highlights: títulos dos resumos (wpp_context, já classificados pelo LLM
  # local via Ollama — zero custo de tokens pagos) gerados pelas mensagens
  # do range de hoje. Só roda se algo novo foi indexado; limitado a 6 pra
  # não virar textão. Pedido do Diego 2026-11-04: "resumo dos principais
  # pontos do dia".
  HIGHLIGHTS=""
  if [ "${IDX_N:-0}" -gt 0 ] 2>/dev/null; then
    HIGHLIGHTS="$(QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}" python3 -c "
import json, os, urllib.request

base = os.environ['QDRANT_URL']
def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req))

day_filter = {'filter': {'must': [{'key': 'day', 'range': {'gte': '$START', 'lt': '$END'}}]}, 'limit': 500, 'with_payload': True}
msgs = post('/collections/wpp_messages/points/scroll', day_filter)['result']['points']
ctx_ids = sorted({p['payload']['context_id'] for p in msgs if p['payload'].get('context_id')})
if not ctx_ids:
    raise SystemExit
ctxs = post('/collections/wpp_context/points', {'ids': ctx_ids[:6], 'with_payload': True})['result']
for p in ctxs:
    pl = p['payload']
    print(f\"• *{pl.get('kind','?')}*: {pl.get('title','')}\")
" 2>/dev/null)"
  fi
  if [ -n "$HIGHLIGHTS" ]; then
    HIGHLIGHTS=$'\n\n'"📝 *Principais pontos do dia*"$'\n'"$HIGHLIGHTS"
  fi

  if [ -z "$LINE" ]; then
    # No summary line in the CLI output = the report CANNOT be trusted.
    # Say so loudly instead of reporting "0 lidas · 0 indexadas" (which is
    # what silently hid 12 successful runs from the user).
    MSG="⚠️ *WPP-CONTEXT-INDEX*: não consegui ler o resumo da execução de ${START} a ${END}. Ver log: ~/Library/Logs/wpp-context-ingest.log"
  else
    MSG="✅ *WPP-CONTEXT-INDEX*

📅 ${START} → ${END}
📨 ${READ_N:-0} msgs lidas · 🗂️ ${IDX_N:-0} indexadas · ⚠️ ${ERR_N:-0} erros${NOTE}${HIGHLIGHTS}"
  fi
else
  MSG="⚠️ *WPP-CONTEXT-INDEX*: ingestão de ${START} a ${END} FALHOU. Ver log: ~/Library/Logs/wpp-context-ingest.log"
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
  printf '%s\n' "$MSG"
else
  "$WPP" send "$TO" "$MSG" >/dev/null 2>&1
fi
printf '%s\n' "$OUT"
exit $STATUS
