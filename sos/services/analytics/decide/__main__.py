"""CLI entry point for the analytics decision agent.

Usage:
    python -m sos.services.analytics.decide --tenant viamar
    python -m sos.services.analytics.decide --tenant viamar --create-tasks
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from sos.services.analytics.decide.agent import DecisionAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sos.analytics.decide.cli")


async def run_decide(tenant: str, create_tasks: bool) -> None:
    """Run the decision agent for a tenant."""
    mirror_url = os.environ.get("MIRROR_URL", "http://localhost:8844")
    mirror_token = os.environ.get("MIRROR_TOKEN", "")

    squad_url = None
    if create_tasks:
        squad_url = os.environ.get("SQUAD_URL", "http://localhost:8060")

    if not mirror_token:
        logger.warning("MIRROR_TOKEN not set — Mirror calls may fail, using mock data")

    agent = DecisionAgent(
        tenant=tenant,
        mirror_url=mirror_url,
        mirror_token=mirror_token,
        squad_url=squad_url,
    )

    try:
        decisions = await agent.run()

        if decisions:
            print(json.dumps(decisions, indent=2))
        else:
            print(f"No decisions generated for {tenant}")
    finally:
        await agent.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SOS Analytics Decision Agent")
    parser.add_argument("--tenant", required=True, help="Tenant slug to analyze")
    parser.add_argument(
        "--create-tasks",
        action="store_true",
        help="Create tasks in Squad Service (default: dry run)",
    )
    args = parser.parse_args()

    asyncio.run(run_decide(args.tenant, args.create_tasks))


if __name__ == "__main__":
    main()
