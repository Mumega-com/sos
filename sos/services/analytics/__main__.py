"""CLI entry point for analytics ingestion.

Usage:
    python -m sos.services.analytics --tenant viamar
    python -m sos.services.analytics --all --days 14
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from sos.services.analytics.ingest import AnalyticsIngester

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sos.analytics.cli")

TENANTS_PATH = Path.home() / ".sos" / "tenants.json"


def load_tenants() -> dict[str, dict[str, str]]:
    """Load tenant configs from ~/.sos/tenants.json.

    Expected format:
    {
        "viamar": {
            "ga4_property_id": "properties/123456",
            "gsc_domain": "sc-domain:viamar.com",
            "clarity_project_id": "abc123"
        },
        "dnu": {
            "ga4_property_id": "properties/789012",
            "gsc_domain": "sc-domain:dentalnearyou.com"
        }
    }
    """
    if not TENANTS_PATH.exists():
        logger.warning("No tenants config at %s", TENANTS_PATH)
        return {}
    try:
        return json.loads(TENANTS_PATH.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse %s: %s", TENANTS_PATH, exc)
        return {}


def run_tenant(name: str, config: dict[str, str], days: int) -> str:
    """Run ingestion for a single tenant."""
    mirror_url = os.environ.get("MIRROR_URL", "http://localhost:8844")
    mirror_token = os.environ.get("MIRROR_TOKEN", "")

    if not mirror_token:
        logger.warning("MIRROR_TOKEN not set — Mirror storage will fail, local fallback will be used")

    ingester = AnalyticsIngester(
        tenant_name=name,
        mirror_url=mirror_url,
        mirror_token=mirror_token,
        ga4_property_id=config.get("ga4_property_id"),
        gsc_domain=config.get("gsc_domain"),
        clarity_project_id=config.get("clarity_project_id"),
    )

    try:
        result = asyncio.run(ingester.ingest_all(days=days))
        return result
    finally:
        ingester.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SOS Analytics Ingestion")
    parser.add_argument("--tenant", help="Tenant slug to ingest")
    parser.add_argument("--all", action="store_true", help="Ingest all tenants")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    args = parser.parse_args()

    if not args.tenant and not args.all:
        parser.error("Specify --tenant NAME or --all")

    tenants = load_tenants()

    if args.tenant:
        config = tenants.get(args.tenant, {})
        if not config:
            logger.warning("Tenant '%s' not in tenants.json, running with env vars", args.tenant)
            config = {
                "ga4_property_id": os.environ.get("GA4_PROPERTY_ID"),
                "gsc_domain": os.environ.get("GSC_DOMAIN"),
                "clarity_project_id": os.environ.get("CLARITY_PROJECT_ID"),
            }
        result = run_tenant(args.tenant, config, args.days)
        if result:
            print(result)
        else:
            print(f"No analytics sources configured for {args.tenant}")
            sys.exit(1)

    elif args.all:
        if not tenants:
            logger.error("No tenants found in %s", TENANTS_PATH)
            sys.exit(1)
        for name, config in tenants.items():
            logger.info("--- Ingesting: %s ---", name)
            result = run_tenant(name, config, args.days)
            if result:
                print(result)
                print()


if __name__ == "__main__":
    main()
