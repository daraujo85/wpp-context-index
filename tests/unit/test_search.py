"""Unit tests for app.services.search.search — Qdrant client + inference.embed
mocked (fast, no network), covers the lexical-boost ranking logic in
isolation."""
from __future__ import annotations

from types import SimpleNamespace

import app.adapters.inference as inference_module
from app.services.search import search


def _point(id_, score, title="", summary="", topics=None):
    payload = {"title": title, "summary": summary, "topics": topics or [],
               "chat": {"id": "c1"}}
    return SimpleNamespace(id=id_, score=score, payload=payload)


class _FakeClient:
    def __init__(self, points):
        self._points = points

    def query_points(self, **kwargs):
        return SimpleNamespace(points=self._points)


def test_search_embeds_query_and_returns_results_sorted_by_score(monkeypatch):
    monkeypatch.setattr(inference_module, "embed", lambda text: [0.1, 0.2])
    client = _FakeClient([
        _point("p1", 0.5, title="Bug checkout"),
        _point("p2", 0.9, title="Deploy"),
    ])

    results = search("bug no checkout", client=client)

    assert [r.context_id for r in results] == ["p2", "p1"]
    assert results[0].title == "Deploy"


def test_search_never_calls_generative_inference(monkeypatch):
    called = {"classify": 0}
    monkeypatch.setattr(inference_module, "embed", lambda text: [0.1])

    def _fail_classify(*a, **kw):
        called["classify"] += 1
        raise AssertionError("search() must never call classify_and_extract")

    monkeypatch.setattr(inference_module, "classify_and_extract", _fail_classify)
    client = _FakeClient([_point("p1", 0.5, title="x")])

    search("query", client=client)

    assert called["classify"] == 0


def test_search_boosts_exact_ticket_id_match_over_semantic_only(monkeypatch):
    monkeypatch.setattr(inference_module, "embed", lambda text: [0.1])
    # semantically closer (higher raw score) but no ticket id in payload
    semantic_only = _point("p1", 0.95, title="Erro parecido", summary="algo relacionado")
    # lower raw score but has the exact ticket id ABC-123 in the title
    exact_match = _point("p2", 0.4, title="Bug ABC-123 no checkout")
    client = _FakeClient([semantic_only, exact_match])

    results = search("status do ABC-123", client=client)

    assert results[0].context_id == "p2"
    assert results[1].context_id == "p1"


def test_search_without_identifier_keeps_pure_semantic_order(monkeypatch):
    monkeypatch.setattr(inference_module, "embed", lambda text: [0.1])
    client = _FakeClient([
        _point("p1", 0.3, title="a"),
        _point("p2", 0.7, title="b"),
    ])

    results = search("pergunta qualquer sem identificador", client=client)

    assert [r.context_id for r in results] == ["p2", "p1"]
