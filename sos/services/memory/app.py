from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sos import __version__
from sos.observability.logging import get_logger
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.services.memory.core import MemoryCore

SERVICE_NAME = "memory"
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

init_tracing("memory")

app = FastAPI(title="SOS Memory Service", version=__version__)
instrument_fastapi(app)

# CORS for desktop/mobile apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

memory = MemoryCore()

@app.on_event("startup")
async def startup_event():
    from sos.services.bus.discovery import register_service
    # Port 6061 based on system check
    await register_service("memory", 6061)

class AddMemoryRequest(BaseModel):
    content: str
    metadata: Dict[str, Any] = {}

class SearchRequest(BaseModel):
    query: str
    limit: int = 5

@app.get("/health")
async def health() -> Dict[str, Any]:
    core_health = await memory.health()
    return {
        "status": "ok",
        "version": __version__,
        "service": SERVICE_NAME,
        "uptime_seconds": time.time() - _START_TIME,
        "core": core_health
    }

@app.get("/state")
async def get_state() -> Dict[str, Any]:
    return await memory.get_arf_state()

@app.post("/add")
async def add_memory(request: AddMemoryRequest):
    item_id = await memory.add(request.content, request.metadata)
    return {"id": item_id, "status": "stored"}

@app.post("/search")
async def search_memory(request: SearchRequest):
    results = await memory.search(request.query, request.limit)
    return {"results": results}

@app.post("/code/search")
async def search_code(request: SearchRequest, repo: Optional[str] = None):
    """Semantic search over the code graph."""
    results = await memory.search_code(request.query, request.limit, repo)
    return {"results": results}