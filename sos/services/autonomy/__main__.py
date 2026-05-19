#!/usr/bin/env python3
"""
Mumega Autonomy Service Runner

Usage:
    python -m sos.services.autonomy
    mumega-autonomy
"""

import asyncio
import os
import signal

from sos.services.autonomy.service import AutonomyService, AutonomyConfig
from sos.observability.logging import get_logger

log = get_logger("autonomy_main")


def main():
    """Main entry point for autonomy service."""
    # Load config from environment
    config = AutonomyConfig(
        agent_id=os.getenv("SOS_AGENT_ID", "agent:River"),
        agent_name=os.getenv("SOS_AGENT_NAME", "River"),
        pulse_interval_seconds=float(os.getenv("SOS_PULSE_INTERVAL", "300")),
        dream_interval_seconds=float(os.getenv("SOS_DREAM_INTERVAL", "3600")),
        enable_dreams=os.getenv("SOS_ENABLE_DREAMS", "true").lower() == "true",
        enable_avatar=os.getenv("SOS_ENABLE_AVATAR", "false").lower() == "true",
        enable_social=os.getenv("SOS_ENABLE_SOCIAL", "false").lower() == "true",
        gateway_url=os.getenv("GATEWAY_URL", "https://gateway.mumega.com/"),
    )

    service = AutonomyService(config=config)

    # Handle shutdown signals
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_shutdown(sig, frame):
        log.info(f"Received {sig}, shutting down...")
        loop.create_task(service.stop())

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    log.info(
        "Starting Mumega Autonomy Service",
        agent=config.agent_id,
        pulse_interval=config.pulse_interval_seconds,
    )

    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        loop.run_until_complete(service.stop())
        loop.close()


if __name__ == "__main__":
    main()
