from __future__ import annotations

import os

import uvicorn

uvicorn.run(
    "sos.services.dashboard.app:app",
    host=os.getenv("SOS_DASHBOARD_HOST", "127.0.0.1"),
    port=8090,
)
