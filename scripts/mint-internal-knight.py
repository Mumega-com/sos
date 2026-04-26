#!/usr/bin/env python3
"""CLI wrapper for internal knight-mint — Sprint 008 S008-A / G76.

Usage:
    AUDIT_INTERNAL_MINT_MODE=1 python3 scripts/mint-internal-knight.py \
        --name gavin --role closer --channel 1234567890 --signer loom

    Batch mint (all 4 initial reps):
    AUDIT_INTERNAL_MINT_MODE=1 python3 scripts/mint-internal-knight.py --batch
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure SOS is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint internal knight for sales rep")
    parser.add_argument("--name", help="Rep name (e.g., gavin)")
    parser.add_argument("--role", help="Rep role (e.g., closer)")
    parser.add_argument("--channel", help="Discord channel ID")
    parser.add_argument("--signer", default="loom", help="Signer (loom or hadi)")
    parser.add_argument("--batch", action="store_true", help="Mint all initial reps")
    args = parser.parse_args()

    from sos.services.billing.internal_knight_mint import mint_internal_knight

    if args.batch:
        # Batch mint: 4 initial reps (5th TBD)
        reps = [
            {"name": "gavin", "role": "closer", "channel": "PLACEHOLDER_GAVIN"},
            {"name": "lex", "role": "activator", "channel": "PLACEHOLDER_LEX"},
            {"name": "noor", "role": "captain", "channel": "PLACEHOLDER_NOOR"},
            {"name": "pricila", "role": "sourcer", "channel": "PLACEHOLDER_PRICILA"},
        ]
        for rep in reps:
            print(f"\n--- Minting {rep['name']}-knight ---")
            result = mint_internal_knight(
                name=rep["name"],
                role=rep["role"],
                discord_channel_id=rep["channel"],
                signer=args.signer or "loom",
            )
            print(json.dumps(result, indent=2))
        return

    if not args.name or not args.role or not args.channel:
        parser.error("--name, --role, and --channel are required (or use --batch)")

    result = mint_internal_knight(
        name=args.name,
        role=args.role,
        discord_channel_id=args.channel,
        signer=args.signer,
    )
    print(json.dumps(result, indent=2))

    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
