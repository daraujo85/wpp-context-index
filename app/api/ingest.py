"""POST /api/v1/ingest and GET /api/v1/ingest/{run_id} — thin pass-through
to services.ingestion.run(), triggered via FastAPI's own BackgroundTasks
(no Celery/Redis, PRD §18). Run status is tracked in an in-memory dict
keyed by a run_id minted here for polling — no new DB/queue, in-memory is
fine per this task's scope. (services.ingestion.run() also mints its own
internal IngestionSummary.run_id; both ids are exposed, no collision risk
since only ours is used as the dict key.)
"""
from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.services import ingestion

router = APIRouter()

_runs: dict[str, dict] = {}


class IngestRequest(BaseModel):
    start: str
    end: str


def _execute(run_id: str, start: str, end: str) -> None:
    try:
        summary = ingestion.run(start, end)
        _runs[run_id] = {"status": "completed", "summary": asdict(summary)}
    except Exception as exc:  # noqa: BLE001 - isolate background failure, report via status
        _runs[run_id] = {"status": "failed", "error": str(exc)}


@router.post("/api/v1/ingest")
def ingest_endpoint(request: IngestRequest, background_tasks: BackgroundTasks) -> dict:
    run_id = uuid.uuid4().hex
    _runs[run_id] = {"status": "running"}
    background_tasks.add_task(_execute, run_id, request.start, request.end)
    return {"run_id": run_id, "status": "running"}


@router.get("/api/v1/ingest/{run_id}")
def ingest_status(run_id: str) -> dict:
    run = _runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    return {"run_id": run_id, **run}
