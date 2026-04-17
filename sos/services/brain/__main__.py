"""Entry point for `python -m sos.services.brain`.

Runs as a systemd --user service (`sos-brain.service`). Never crashes on
handler errors — always logs + skips + continues.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from sos.services.brain.service import BrainService


def _setup_logging() -> None:
    level = os.environ.get("SOS_LOG_LEVEL", "info").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [brain] %(levelname)s %(message)s",
    )


async def _run() -> int:
    _setup_logging()
    service = BrainService()

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def _stop(sig_name: str) -> None:
        logging.info("received %s, shutting down", sig_name)
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: _stop(s.name))

    service_task = asyncio.create_task(service.run())
    await stopping.wait()
    service.stop()
    try:
        await asyncio.wait_for(service_task, timeout=10.0)
    except asyncio.TimeoutError:
        service_task.cancel()
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
