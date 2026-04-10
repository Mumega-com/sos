"""CLI entry point for the SOS Feedback Loop.

Usage:
    python -m sos.services.feedback --tenant viamar
    python -m sos.services.feedback --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from sos.services.feedback.loop import FeedbackLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sos.feedback.cli")

TENANTS_PATH = Path.home() / ".sos" / "tenants.json"


def load_tenants() -> dict[str, dict[str, str]]:
    """Load tenant configs from ~/.sos/tenants.json."""
    if not TENANTS_PATH.exists():
        logger.warning("No tenants config at %s", TENANTS_PATH)
        return {}
    try:
        return json.loads(TENANTS_PATH.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse %s: %s", TENANTS_PATH, exc)
        return {}


async def run_feedback(tenant: str) -> dict[str, object]:
    """Run the feedback loop for a single tenant."""
    mirror_url = os.environ.get("MIRROR_URL", "http://localhost:8844")
    mirror_token = os.environ.get("MIRROR_TOKEN", "")

    if not mirror_token:
        logger.warning("MIRROR_TOKEN not set — Mirror calls may fail, using mock data")

    loop = FeedbackLoop(
        tenant=tenant,
        mirror_url=mirror_url,
        mirror_token=mirror_token,
    )

    try:
        report = await loop.run()
        return report
    finally:
        await loop.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SOS Feedback Loop")
    parser.add_argument("--tenant", help="Tenant slug to run feedback for")
    parser.add_argument("--all", action="store_true", help="Run feedback for all tenants")
    args = parser.parse_args()

    if not args.tenant and not args.all:
        parser.error("Specify --tenant NAME or --all")

    if args.tenant:
        report = asyncio.run(run_feedback(args.tenant))
        summary = report.get("summary", {})
        print(json.dumps(summary, indent=2))

    elif args.all:
        tenants = load_tenants()
        if not tenants:
            logger.error("No tenants found in %s", TENANTS_PATH)
            sys.exit(1)

        async def run_all() -> None:
            for name in tenants:
                logger.info("--- Feedback: %s ---", name)
                report = await run_feedback(name)
                summary = report.get("summary", {})
                print(json.dumps(summary, indent=2))
                print()

        asyncio.run(run_all())


if __name__ == "__main__":
    main()
