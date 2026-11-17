"""Integration tests for scripts/wpp-context-ingest-notify.sh — the cron
wrapper that turns the CLI's stdout into the daily WhatsApp report.

Regression target (2026-11-17): every one of the 12 daily reports sent
"0 msgs lidas · 0 indexadas · 0 erros" even on runs that read 268 msgs and
indexed 53 units. Cause: Rich's Live panel render does NOT end with a newline
when stdout isn't a TTY (cron pipes it), so the CLI's machine-readable
summary landed glued to the panel's closing border ("╯ingest: range ...")
and the wrapper's anchored `grep '^ingest: range'` never matched — a parse
failure that looked exactly like "nothing happened".

The wrapper runs for real here (no network): WPP_CONTEXT_CMD replaces the
CLI with a stub, DRY_RUN=1 prints the message instead of sending it, and the
allowlist sync + Qdrant highlights are pointed away from the real targets.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wpp-context-ingest-notify.sh"

# Real CLI output shape: the summary line glued to the panel's closing border
# (Rich's render has no trailing newline in non-TTY mode).
GLUED_OUTPUT = """╭───────────────── wpp-context ingest  [1/1] ─────────────────╮
│ lidas 268 · pré-filtro 14 · indexadas 53 · erros 0           │
╰──────────────────────────────── 03:29:24 ─────────────────────╯ingest: range 2026-11-16..2026-11-17 run_id=1c03dea6377249fd832948baafdc7a28 messages_read=268 contexts_indexed=53 errors=0
total por contato (só esta execução):
  camila (dm): 1 msg
  lenilson (dm): 49 msg
  lucas (dm): 49 msg
  garagem (grupo): 8 msg
  prata_h_levi (grupo): 33 msg
  python (grupo): 1 msg
"""


def _run_wrapper(tmp_path: Path, cli_output: str) -> str:
    stub = tmp_path / "stub-cli.sh"
    stub.write_text(f"#!/usr/bin/env bash\ncat <<'EOF'\n{cli_output}EOF\n")
    stub.chmod(0o755)

    env = {
        **os.environ,
        "INGEST_START": "2026-11-16",
        "INGEST_END": "2026-11-17",
        "WPP_CONTEXT_CMD": str(stub),
        "WPP_SYNC_SCRIPT": "",  # don't touch the real whatsapp.env
        "WPP_SEND_SCRIPT": str(tmp_path / "no-such-sender.sh"),
        "WPP_INGEST_LOG": str(tmp_path / "ingest.log"),
        "QDRANT_URL": "http://127.0.0.1:1",  # highlights query fails fast -> empty
        "DRY_RUN": "1",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_report_counts_come_from_the_glued_summary_line(tmp_path):
    out = _run_wrapper(tmp_path, GLUED_OUTPUT)
    assert "268 msgs lidas" in out
    assert "53 indexadas" in out
    assert "⚠️ 0 erros" in out


def test_report_lists_sources_by_volume(tmp_path):
    out = _run_wrapper(tmp_path, GLUED_OUTPUT)
    assert "• *PRATA_H_LEVI*: 33 msg" in out
    assert "• *LENILSON*: 49 msg" in out
    # groups and contacts in separate sections
    assert "📢 *Grupos* (3 · 42 msg)" in out
    assert "👤 *Contatos* (3 · 99 msg)" in out


def test_missing_summary_line_reports_failure_not_zero(tmp_path):
    """A report that can't parse the CLI summary must say so — silently
    reporting "0 msgs lidas" is what hid 12 successful runs."""
    out = _run_wrapper(tmp_path, "total por contato (só esta execução):\n  camila (dm): 1 msg\n")
    assert "não consegui ler o resumo" in out
    assert "msgs lidas" not in out
