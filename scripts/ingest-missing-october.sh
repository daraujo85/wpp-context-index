#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DAYS=(
  "2026-10-01 2026-10-02"
  "2026-10-02 2026-10-03"
  "2026-10-03 2026-10-04"
  "2026-10-04 2026-10-05"
  "2026-10-05 2026-10-06"
  "2026-10-06 2026-10-07"
  "2026-10-07 2026-10-08"
  "2026-10-08 2026-10-09"
  "2026-10-09 2026-10-10"
)

for pair in "${DAYS[@]}"; do
  read -r start end <<< "$pair"
  echo "=================================================="
  echo "🚀 Iniciando ingestão para: $start -> $end"
  echo "=================================================="
  INGEST_START="$start" INGEST_END="$end" ./scripts/ingest-yesterday.sh
  echo "✅ Concluído: $start -> $end"
done

echo "🎉 Todos os dias pendentes de Outubro/2026 foram processados!"
