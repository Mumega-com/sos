"""CLI for the ToRivers-SOS Bridge.

Usage:
    python -m sos.adapters.torivers --list
    python -m sos.adapters.torivers --register-all
    python -m sos.adapters.torivers --execute monthly-seo-audit --input '{"domain":"viamar.com"}'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from sos.adapters.torivers.bridge import ToRiversBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def _build_bridge() -> ToRiversBridge:
    """Build a bridge instance from environment or defaults."""
    return ToRiversBridge(
        squad_url=os.environ.get("SOS_SQUAD_URL", "http://localhost:8060"),
        bus_url=os.environ.get("SOS_BUS_URL", "http://localhost:6380"),
        bus_token=os.environ.get("SOS_BUS_TOKEN", "dev-token"),
        torivers_api_url=os.environ.get("TORIVERS_API_URL"),
        objectives_url=os.environ.get("SOS_OBJECTIVES_URL"),
    )


async def _cmd_list() -> None:
    """List all available SOS workflows for ToRivers."""
    workflows = ToRiversBridge.list_available_workflows()
    print(f"\n{'='*60}")
    print("Available SOS Workflows for ToRivers Marketplace")
    print(f"{'='*60}\n")
    for wf in workflows:
        print(f"  {wf['name']}")
        print(f"    {wf['description']}")
        print(f"    Price: ${wf.get('price', 0):.2f}/run")
        print(f"    SOS Service: {wf['sos_service']}")
        print()


async def _cmd_register_all() -> None:
    """Register all workflows on ToRivers."""
    bridge = _build_bridge()
    ids = await bridge.register_all()
    print(f"\nRegistered {len(ids)} workflows:")
    for automation_id in ids:
        print(f"  {automation_id}")


async def _cmd_execute(workflow_name: str, input_json: str) -> None:
    """Execute a specific workflow."""
    bridge = _build_bridge()

    # Find and register the requested workflow
    workflows = ToRiversBridge.list_available_workflows()
    target = None
    for wf in workflows:
        if wf["name"] == workflow_name:
            target = wf
            break

    if target is None:
        print(f"Error: unknown workflow '{workflow_name}'", file=sys.stderr)
        print("Available workflows:", file=sys.stderr)
        for wf in workflows:
            print(f"  {wf['name']}", file=sys.stderr)
        sys.exit(1)

    automation_id = await bridge.register_workflow(target)

    try:
        input_data = json.loads(input_json)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON input: {exc}", file=sys.stderr)
        sys.exit(1)

    result = await bridge.execute(automation_id, input_data, user_id="cli-user")
    print("\nExecution result:")
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ToRivers-SOS Bridge CLI",
        prog="python -m sos.adapters.torivers",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List available workflows")
    group.add_argument(
        "--register-all",
        action="store_true",
        help="Register all workflows on ToRivers",
    )
    group.add_argument("--execute", metavar="WORKFLOW", help="Execute a workflow by name")
    parser.add_argument(
        "--input",
        metavar="JSON",
        default="{}",
        help="JSON input data for --execute",
    )

    args = parser.parse_args()

    if args.list:
        asyncio.run(_cmd_list())
    elif args.register_all:
        asyncio.run(_cmd_register_all())
    elif args.execute:
        asyncio.run(_cmd_execute(args.execute, args.input))


if __name__ == "__main__":
    main()
