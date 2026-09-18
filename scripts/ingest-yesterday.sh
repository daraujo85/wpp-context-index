#!/usr/bin/env bash
# Cron wrapper for T7's `wpp-context ingest` CLI (PRD §18: host cron only,
# no Celery/Redis/scheduler). Computes "yesterday 00:00 -> today 00:00"
# in local time and delegates to the CLI unchanged.
#
# Override hooks (for manual smoke testing without waiting a day / without
# full infra up):
#   INGEST_START / INGEST_END  - force the ISO dates instead of computing them
#   WPP_CONTEXT_CMD            - command to run instead of "python -m app.cli"
#                                 (e.g. "echo" for a dry run)
set -euo pipefail

cd "$(dirname "$0")/.."

if date -v-1d +%F >/dev/null 2>&1; then
  YESTERDAY_CMD=(date -v-1d +%F)   # BSD date (macOS)
else
  YESTERDAY_CMD=(date -d yesterday +%F)  # GNU date (Linux)
fi

START="${INGEST_START:-$("${YESTERDAY_CMD[@]}")}"
END="${INGEST_END:-$(date +%F)}"

# This script runs the CLI on the HOST, not inside the docker-compose
# container — so it needs host-perspective URLs, not the container-perspective
# defaults in .env/docker-compose.yml (host.docker.internal:11434,
# qdrant:6333, neither of which resolve from bare host). Exported vars win
# over .env (config.py uses os.environ.setdefault).
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"

# shellcheck disable=SC2086 # WPP_CONTEXT_CMD is intentionally word-split
exec ${WPP_CONTEXT_CMD:-python -m app.cli} ingest --start "$START" --end "$END"
