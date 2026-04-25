from __future__ import annotations

import asyncio
import os

import uvicorn
from sos.services.engine.app import app


def main() -> None:
    host = os.getenv("SOS_ENGINE_HOST", "0.0.0.0")
    port = int(os.getenv("SOS_ENGINE_PORT", "6060"))
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=os.getenv("SOS_LOG_LEVEL", "info"),
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()

