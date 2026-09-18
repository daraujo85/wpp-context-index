"""FastAPI app entrypoint."""
from __future__ import annotations

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.api.search import router as search_router
from app.api.source import router as source_router

app = FastAPI()
app.include_router(health_router)
app.include_router(search_router)
app.include_router(ingest_router)
app.include_router(source_router)
