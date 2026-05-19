"""Run with: python -m sos.services.dispatcher"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "sos.services.dispatcher.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("SOS_DISPATCHER_PORT", "6071")),
        log_level="info",
    )
