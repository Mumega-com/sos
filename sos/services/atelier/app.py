import os
import time
from typing import Any, Dict
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from sos import __version__
from sos.observability.logging import get_logger

SERVICE_NAME = "atelier"
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

app = FastAPI(title="SOS Atelier Service", version=__version__)

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "service": SERVICE_NAME,
        "uptime_seconds": time.time() - _START_TIME,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
