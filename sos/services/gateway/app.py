"""
SOS Gateway Service Runner

Runs the Gateway service which includes:
- Bridge API for external agents (port 6062)
- MCP Gateway (port 6063)

Usage:
    python -m sos.services.gateway.app
    SOS_BRIDGE_PORT=6062 python -m sos.services.gateway.app
"""

import os
import uvicorn

from sos.services.gateway.bridge import app
from sos.observability.logging import get_logger

log = get_logger("gateway_app")


def main():
    """Run the gateway service."""
    port = int(os.environ.get("SOS_BRIDGE_PORT", "6062"))
    host = os.environ.get("SOS_BRIDGE_HOST", "0.0.0.0")

    log.info(f"Starting SOS Gateway Bridge on {host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
