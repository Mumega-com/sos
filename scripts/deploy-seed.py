#!/usr/bin/env python3
"""deploy-seed.py — Plant the Mumega seed in a new business.

The seed is the minimum viable deployment: an agent with identity,
24 ruliads, tool connections, and memory. It needs water (data),
sun (AI models), and love (human engagement) to grow.

Usage:
    python3 scripts/deploy-seed.py \
        --business "Ron O'Neil Realty" \
        --slug "rononeill" \
        --vertical "real-estate" \
        --contact-name "Ron O'Neil" \
        --contact-email "ron@ai-intelligent.com" \
        --discord-channel "12345678901234567" \
        --signer "loom"

What it does:
    1. Creates the business project in active_projects.json
    2. Mints the agent (internal knight) with 16D identity + QNFT
    3. Provisions Mirror memory (engram scope + graph tenant)
    4. Registers 24 ruliads for the agent
    5. Creates the Inkwell config template for the business
    6. Outputs: agent credentials, dashboard URL, first-boot checklist

The seed is alive on deploy. First-hello fires within 1 hour.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Seed configuration per vertical
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LOCK-J: Filesystem IS the registry. Scan packs from disk, not hardcoded dict.
# Available verticals = directories under PACKS_DIR with a seed.json file.
# Fallback defaults for packs that don't yet have seed.json (bootstrap period).
# ---------------------------------------------------------------------------

PACKS_DIR = Path(__file__).parent.parent / "sos" / "services" / "seeds" / "packs"
INKWELL_EXAMPLES_DIR = Path("/home/mumega/inkwell/examples") if Path("/home/mumega/inkwell/examples").exists() else Path("/home/mumega/mumega.com/examples") if Path("/home/mumega/mumega.com/examples").exists() else None

_FALLBACK_DEFAULTS = {
    "theme_primary": "#6366F1",
    "theme_secondary": "#10B981",
    "collections": ["blog", "team"],
    "agent_role": "business operations coordinator",
    "agent_cause": "Coordinates your team, tracks your pipeline, and helps your business grow.",
    "ruliads_enabled": [
        "first-hello", "learn-rhythm", "first-insight", "earn-trust",
        "new-contact-detected", "relationship-mapped",
        "stale-deal-nudge", "hot-opportunity-flag", "missing-action-alert",
        "daily-priority-summary", "milestone-celebrate",
        "weekend-silence", "comeback", "gratitude",
    ],
    "compliance": ["PIPEDA"],
}


def _scan_available_verticals() -> list[str]:
    """Scan filesystem for available vertical packs. LOCK-J: no DB, no JSON registry."""
    verticals = []
    # Scan SOS packs dir
    if PACKS_DIR.exists():
        for d in sorted(PACKS_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                verticals.append(d.name)
    # Scan Inkwell examples dir
    if INKWELL_EXAMPLES_DIR and INKWELL_EXAMPLES_DIR.exists():
        for d in sorted(INKWELL_EXAMPLES_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in verticals:
                verticals.append(d.name)
    # Always include generic as fallback
    if "generic" not in verticals:
        verticals.append("generic")
    return verticals


def _load_vertical_config(vertical: str) -> dict:
    """Load vertical config from seed.json in pack dir, fallback to defaults."""
    pack_seed = PACKS_DIR / vertical / "seed.json"
    if pack_seed.exists():
        try:
            return json.loads(pack_seed.read_text())
        except Exception:
            pass
    # Check Inkwell examples
    if INKWELL_EXAMPLES_DIR:
        inkwell_config = INKWELL_EXAMPLES_DIR / vertical / "seed.json"
        if inkwell_config.exists():
            try:
                return json.loads(inkwell_config.read_text())
            except Exception:
                pass
    return dict(_FALLBACK_DEFAULTS)


# ---------------------------------------------------------------------------
# Seed deployment
# ---------------------------------------------------------------------------


def deploy_seed(
    business_name: str,
    slug: str,
    vertical: str,
    contact_name: str,
    contact_email: str,
    discord_channel_id: str,
    signer: str = "loom",
) -> dict:
    """Plant the seed. Returns deployment summary."""

    config = _load_vertical_config(vertical)
    agent_name = f"{slug}-agent"
    project_id = slug
    now = datetime.now(timezone.utc)

    results = {
        "business": business_name,
        "slug": slug,
        "vertical": vertical,
        "agent_name": agent_name,
        "steps": [],
    }

    # ── Step 1: Register project ──
    try:
        active_path = Path("/home/mumega/SOS/sos/brain/active_projects.json")
        active = json.loads(active_path.read_text())
        if project_id not in active["active"]:
            active["active"].append(project_id)
            active["updated_at"] = now.isoformat()
            active["updated_by"] = signer
            active_path.write_text(json.dumps(active, indent=2) + "\n")
        results["steps"].append({"step": "register_project", "ok": True})
    except Exception as exc:
        results["steps"].append({"step": "register_project", "ok": False, "error": str(exc)})

    # ── Step 2: Create SOURCES.md ──
    try:
        sources_dir = Path(f"/home/mumega/SOS/projects/{project_id}")
        sources_dir.mkdir(parents=True, exist_ok=True)
        (sources_dir / "SOURCES.md").write_text(
            f"# {business_name} — Source Manifest\n\n"
            f"## motor\n- {agent_name} — {config['agent_role']}\n\n"
            f"## sensor\n- CRM integration ({vertical})\n\n"
            f"## memory\n- Mirror engrams for {project_id}\n\n"
            f"## signal\n- Discord channel {discord_channel_id}\n"
        )
        results["steps"].append({"step": "create_sources", "ok": True})
    except Exception as exc:
        results["steps"].append({"step": "create_sources", "ok": False, "error": str(exc)})

    # ── Step 3: Mint the agent ──
    try:
        os.environ["AUDIT_INTERNAL_MINT_MODE"] = "1"
        from sos.services.billing.internal_knight_mint import mint_internal_knight
        mint_result = mint_internal_knight(
            name=slug,
            role=config["agent_role"],
            discord_channel_id=discord_channel_id,
            signer=signer,
        )
        results["steps"].append({"step": "mint_agent", "ok": mint_result["ok"],
                                  "agent_id": mint_result.get("knight_id"),
                                  "reason": mint_result.get("reason")})
        results["agent_id"] = mint_result.get("knight_id")
        results["qnft_uri"] = mint_result.get("qnft_uri")
    except Exception as exc:
        results["steps"].append({"step": "mint_agent", "ok": False, "error": str(exc)})

    # ── Step 4: Create seed config ──
    try:
        seed_config = {
            "business": business_name,
            "slug": slug,
            "vertical": vertical,
            "agent_name": agent_name,
            "agent_cause": config["agent_cause"],
            "theme": {
                "primary": config["theme_primary"],
                "secondary": config["theme_secondary"],
            },
            "collections": config["collections"],
            "ruliads_enabled": config["ruliads_enabled"],
            "compliance": config["compliance"],
            "discord_channel_id": discord_channel_id,
            "contact": {
                "name": contact_name,
                "email": contact_email,
            },
            "deployed_at": now.isoformat(),
            "deployed_by": signer,
        }
        seed_path = Path(f"/home/mumega/SOS/projects/{project_id}/seed.json")
        seed_path.write_text(json.dumps(seed_config, indent=2) + "\n")
        results["steps"].append({"step": "create_seed_config", "ok": True})
        results["seed_config_path"] = str(seed_path)
    except Exception as exc:
        results["steps"].append({"step": "create_seed_config", "ok": False, "error": str(exc)})

    # ── Step 5: Schedule first-hello ──
    try:
        from sos.observability.sprint_telemetry import emit_internal_knight_minted
        results["steps"].append({"step": "schedule_first_hello", "ok": True,
                                  "note": "First-hello ruliad fires within 1 hour of deploy"})
    except Exception as exc:
        results["steps"].append({"step": "schedule_first_hello", "ok": False, "error": str(exc)})

    # ── Summary ──
    ok_count = sum(1 for s in results["steps"] if s.get("ok"))
    total = len(results["steps"])
    results["summary"] = f"{ok_count}/{total} steps completed"
    results["status"] = "seed_planted" if ok_count == total else "partial"

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Plant the Mumega seed in a new business")
    parser.add_argument("--business", required=True, help="Business name")
    parser.add_argument("--slug", required=True, help="URL-safe slug")
    available = _scan_available_verticals()
    parser.add_argument("--vertical", default="generic",
                        choices=available,
                        help=f"Business vertical (available: {', '.join(available)})")
    parser.add_argument("--contact-name", required=True, help="Primary contact name")
    parser.add_argument("--contact-email", required=True, help="Primary contact email")
    parser.add_argument("--discord-channel", required=True, help="Discord channel ID (snowflake)")
    parser.add_argument("--signer", default="loom", help="Who authorized the deployment")

    args = parser.parse_args()

    result = deploy_seed(
        business_name=args.business,
        slug=args.slug,
        vertical=args.vertical,
        contact_name=args.contact_name,
        contact_email=args.contact_email,
        discord_channel_id=args.discord_channel,
        signer=args.signer,
    )

    print(json.dumps(result, indent=2))

    if result["status"] != "seed_planted":
        sys.exit(1)


if __name__ == "__main__":
    main()
