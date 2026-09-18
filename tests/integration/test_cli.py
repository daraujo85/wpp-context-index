"""Integration tests for the CLI (T7): argparse dispatch + stdout, no
services/* yet (T13-T14) so ingest/search/inspect/source are thin stubs."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.cli.__main__ import main


def test_ingest_parses_dates_and_prints_summary(capsys):
    with patch("app.cli.__main__.Settings.sources", return_value=[{"alias": "a", "jid": "x", "is_group": False}]):
        code = main(["ingest", "--start", "2024-01-01", "--end", "2024-01-31"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2024-01-01" in out
    assert "2024-01-31" in out
    assert "1" in out  # source count from Settings().sources()


def test_ingest_rejects_bad_date(capsys):
    with pytest.raises(SystemExit):
        main(["ingest", "--start", "not-a-date", "--end", "2024-01-31"])


def test_search_prints_query(capsys):
    code = main(["search", "hello world"])
    out = capsys.readouterr().out
    assert code == 0
    assert "hello world" in out


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
