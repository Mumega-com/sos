from __future__ import annotations

import asyncio
import os
import socket
import time

import uvicorn
from sos.services.engine.app import app


def main() -> None:
    host = os.getenv("SOS_ENGINE_HOST", "0.0.0.0")
    port = int(os.getenv("SOS_ENGINE_PORT", "6060"))

    # G80: port-6060 contention fix — retry bind with backoff on EADDRINUSE.
    # After kill-9, socket may be in TIME_WAIT for up to 60s. Retry up to 5
    # times with 2s backoff instead of crashing on first attempt.
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # Pre-check: can we bind to the port?
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_sock.bind((host, port))
            test_sock.close()
            break  # Port available
        except OSError:
            if attempt < max_retries - 1:
                wait = 2 * (attempt + 1)
                print(f"Port {port} busy, retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                print(f"Port {port} still busy after {max_retries} attempts, starting anyway (uvicorn may handle)")

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
