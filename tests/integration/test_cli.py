"""Integration tests for the CLI (T7/T15): argparse dispatch + stdout.
ingest/search delegate to services.ingestion.run()/services.search.search()
(T15) — mocked here, same pattern as tests/integration/test_health.py's
adapter mocking. inspect/source stay stubs (M3 scope)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.cli.__main__ import main
from app.services.ingestion import IngestionSummary
from app.services.search import SearchResult


def test_ingest_parses_dates_and_prints_summary(capsys):
    fake_summary = IngestionSummary(
        run_id="run-1", messages_read=5, contexts_indexed=2, errors=0,
    )
    with patch("app.cli.__main__.ingestion.run", return_value=fake_summary) as mock_run:
        code = main(["ingest", "--start", "2024-01-01", "--end", "2024-01-31"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2024-01-01" in out
    assert "2024-01-31" in out
    assert "run-1" in out
    mock_run.assert_called_once_with("2024-01-01T00:00:00", "2024-01-31T00:00:00")


def test_ingest_rejects_bad_date(capsys):
    with pytest.raises(SystemExit):
        main(["ingest", "--start", "not-a-date", "--end", "2024-01-31"])


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
