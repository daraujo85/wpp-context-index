"""Integration tests for the CLI (T7/T15): argparse dispatch + stdout.
ingest/search delegate to services.ingestion.run()/services.search.search()
(T15) — mocked here, same pattern as tests/integration/test_health.py's
adapter mocking. inspect/source stay stubs (M3 scope)."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from app.cli.__main__ import main
from app.services.ingestion import IngestionSummary
from app.services.search import SearchResult

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ingest_parses_dates_and_prints_summary(capsys):
    fake_summary = IngestionSummary(
        run_id="run-1", messages_read=5, contexts_indexed=2, errors=0,
    )
    with patch("app.cli.__main__.ingestion.run", return_value=fake_summary) as mock_run:
        code = main(["ingest", "--start", "2024-01-01", "--end", "2024-01-02"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2024-01-01" in out
    assert "2024-01-02" in out
    assert "run-1" in out
    # on_progress: the live TUI callback wired in main() — identity doesn't matter here
    mock_run.assert_called_once_with(
        "2024-01-01T00:00:00", "2024-01-02T00:00:00", on_progress=ANY
    )


def test_ingest_rejects_bad_date(capsys):
    with pytest.raises(SystemExit):
        main(["ingest", "--start", "not-a-date", "--end", "2024-01-31"])


def test_ingest_splits_the_window_into_one_run_per_day(capsys):
    """Per-day runs are what makes the unattended catch-up work: the
    ingestion state is keyed by (alias, day), so a night that missed a day
    leaves it unmarked and the next night reprocesses exactly that day."""
    fake_summary = IngestionSummary(run_id="run-1")
    with patch("app.cli.__main__.ingestion.run", return_value=fake_summary) as mock_run:
        main(["ingest", "--start", "2024-01-01", "--end", "2024-01-04"])
    assert [call.args for call in mock_run.call_args_list] == [
        ("2024-01-01T00:00:00", "2024-01-02T00:00:00"),
        ("2024-01-02T00:00:00", "2024-01-03T00:00:00"),
        ("2024-01-03T00:00:00", "2024-01-04T00:00:00"),
    ]


def test_ingest_aggregates_counts_across_the_window(capsys):
    days = [
        IngestionSummary(run_id="r1", messages_read=10, contexts_indexed=2, errors=0),
        IngestionSummary(run_id="r2", messages_read=5, contexts_indexed=1, errors=1),
    ]
    with patch("app.cli.__main__.ingestion.run", side_effect=days):
        main(["ingest", "--start", "2024-01-01", "--end", "2024-01-03"])
    out = capsys.readouterr().out
    assert "messages_read=15" in out
    assert "contexts_indexed=3" in out
    assert "errors=1" in out


def test_ingest_summary_is_on_its_own_line(capsys):
    """Regression (2026-11-17): the machine-readable summary must start at
    column 0. Rich's Live panel render does NOT end with a newline when
    stdout isn't a TTY (cron pipes it), so the summary used to land glued to
    the panel's closing border ("╯ingest: range ...") — and the daily report
    wrapper's `grep '^ingest: range'` never matched, making 12 successful
    runs look like "0 msgs lidas · 0 indexadas"."""
    fake_summary = IngestionSummary(
        run_id="run-1", messages_read=5, contexts_indexed=2, errors=0,
    )
    with patch("app.cli.__main__.ingestion.run", return_value=fake_summary):
        main(["ingest", "--start", "2024-01-01", "--end", "2024-01-31"])
    out = capsys.readouterr().out
    assert any(line.startswith("ingest: range") for line in out.splitlines())


def test_ingest_summary_survives_piped_stdout():
    """Same regression, but through the REAL pipeline: the cron wrapper
    pipes the CLI's stdout (not a TTY), which is what makes Rich render the
    panel without a trailing newline. pytest's capsys doesn't reproduce it
    (no panel at all there), so run the CLI in a subprocess with a pipe."""
    snippet = textwrap.dedent(
        """
        from unittest.mock import patch
        from app.cli.__main__ import main
        from app.services.ingestion import IngestionSummary

        fake = IngestionSummary(run_id="run-1", messages_read=5, contexts_indexed=2, errors=0)
        source = {"alias": "x", "jid": "x@c.us", "is_group": False}

        def _fake_run(start, end, on_progress=None):
            # the panel only has content once the CLI updates the Live
            # renderable (on_progress) — without it Rich renders nothing
            # and the "glued to the panel border" condition never happens
            if on_progress:
                on_progress("start", 1, 1, source, fake)
                on_progress("done", 1, 1, source, fake)
            return fake

        with patch("app.cli.__main__.ingestion.run", side_effect=_fake_run):
            main(["ingest", "--start", "2024-01-01", "--end", "2024-01-31"])
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert any(line.startswith("ingest: range") for line in lines), (
        f"summary not at column 0; tail={result.stdout[-300:]!r}"
    )


def test_search_prints_query(capsys):
    fake_results = [
        SearchResult(context_id="ctx1", score=0.9, title="T", summary="S", chat_id="c1"),
    ]
    with patch("app.cli.__main__.search_service.search", return_value=fake_results) as mock_search:
        code = main(["search", "hello world"])
    out = capsys.readouterr().out
    assert code == 0
    assert "hello world" in out
    assert "ctx1" in out
    mock_search.assert_called_once_with("hello world")


def test_inspect_prints_id(capsys):
    code = main(["inspect", "msg-123"])
    out = capsys.readouterr().out
    assert code == 0
    assert "msg-123" in out


def test_source_prints_id(capsys):
    code = main(["source", "msg-123"])
    out = capsys.readouterr().out
    assert code == 0
    assert "msg-123" in out


def test_no_command_exits_nonzero():
    with pytest.raises(SystemExit):
        main([])

def test_sparkline_survives_zero_and_negative_samples():
    """Regression (2026-11-17): a negative delta made `v / peak` negative
    -> int() -> negative index -> IndexError, which crashed the whole
    ingest run mid-flight (the catch-up window died on a rendering bug).
    A rendering bug must never kill the night."""
    from app.cli.__main__ import _sparkline

    assert _sparkline([0.0] * 40)                       # zero peak
    assert _sparkline([-5.0, 0.0, 3.0])                 # negative delta
    assert _sparkline([]) == ""                         # empty -> empty, no raise
    assert len(_sparkline([1.0] * 40)) == 40
