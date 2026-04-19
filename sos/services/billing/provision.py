"""Tenant provisioning — creates full workstation from Stripe payment.

Calls tenant-setup.sh with the right parameters.
Generates Cloudflare tokens.
Sends welcome email/bus message.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("sos.billing.provision")

TENANT_SETUP_SCRIPT = Path.home() / "SOS" / "scripts" / "tenant-setup.sh"

# Cloudflare minter config (from env)
CF_MINTER_TOKEN = os.environ.get("CF_MINTER_TOKEN", "")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "e39eaf94f33092c4efd029d94ae1e9dd")
CF_D1_READ_PERMISSION = "192192df92ee43ac90f2aeeffce67e35"
CF_D1_WRITE_PERMISSION = "09b2857d1c31407795e75e3fed8617a1"


async def _run_tenant_setup(slug: str) -> tuple[bool, str]:
    """Run tenant-setup.sh via subprocess. Returns (success, output)."""
    cmd = [
        "sudo", "bash", str(TENANT_SETUP_SCRIPT),
        slug, "--role", "operator", "--skills", "general",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode("utf-8", errors="replace")
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        log.error("tenant-setup.sh timed out for %s", slug)
        return False, "timeout"
    except Exception as exc:
        log.error("tenant-setup.sh failed for %s: %s", slug, exc)
        return False, str(exc)


async def _mint_cf_token(slug: str) -> str:
    """Mint a scoped Cloudflare API token for the tenant's D1 access."""
    if not CF_MINTER_TOKEN:
        log.warning("CF_MINTER_TOKEN not set, skipping CF token minting")
        return ""

    payload = {
        "name": f"sos-tenant-{slug}-d1",
        "policies": [
            {
                "effect": "allow",
                "resources": {f"com.cloudflare.api.account.{CF_ACCOUNT_ID}": "*"},
                "permission_groups": [
                    {"id": CF_D1_READ_PERMISSION},
                    {"id": CF_D1_WRITE_PERMISSION},
                ],
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.cloudflare.com/client/v4/user/tokens",
                headers={
                    "Authorization": f"Bearer {CF_MINTER_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = resp.json()
            if data.get("success"):
                token_value = data["result"]["value"]
                log.info("Minted CF token for tenant %s", slug)
                return token_value
            log.error("CF token minting failed for %s: %s", slug, data.get("errors"))
            return ""
    except Exception as exc:
        log.error("CF token minting error for %s: %s", slug, exc)
        return ""


def _append_cf_token_to_env(slug: str, cf_token: str) -> None:
    """Append Cloudflare token to the tenant's .sos/.env file."""
    if not cf_token:
        return
    env_path = Path(f"/home/{slug}/.sos/.env")
    if not env_path.exists():
        log.warning("Tenant env not found at %s", env_path)
        return
    with env_path.open("a") as f:
        f.write(f"\n# Cloudflare D1 (auto-provisioned)\nCF_API_TOKEN={cf_token}\n")
    log.info("Appended CF token to %s", env_path)


async def provision_tenant(slug: str, label: str, email: str) -> dict[str, Any]:
    """Full tenant provisioning pipeline.

    1. Run tenant-setup.sh (Linux user, tokens, MCP config, routing)
    2. Mint Cloudflare D1 token
    3. Append CF token to tenant env
    4. Return all credentials
    """
    result: dict[str, Any] = {"slug": slug, "label": label, "email": email}

    # 1. Run tenant-setup.sh — creates user, tokens, squad, routing
    success, output = await _run_tenant_setup(slug)
    result["tenant_setup"] = "ok" if success else "failed"
    if not success:
        log.error("Tenant setup failed for %s: %s", slug, output)
        result["error"] = f"tenant-setup.sh failed: {output[:500]}"
        return result

    # Parse tokens from tenant-setup.sh output
    bus_token = ""
    mirror_token = ""
    for line in output.splitlines():
        if "Bus Token:" in line:
            bus_token = line.split("Bus Token:")[-1].strip()
        elif "Mirror:" in line and "sk-mumega-" in line:
            mirror_token = line.split("Mirror:")[-1].strip()

    result["bus_token"] = bus_token
    result["mirror_token"] = mirror_token

    # 2. Mint Cloudflare D1 token
    cf_token = await _mint_cf_token(slug)
    result["cf_token"] = cf_token

    # 3. Append CF token to tenant env
    _append_cf_token_to_env(slug, cf_token)

    # 4. Build response
    home_dir = f"/home/{slug}"
    mcp_url = f"https://mcp.mumega.com/sse/{bus_token}" if bus_token else ""
    result["mcp_url"] = mcp_url
    result["home_dir"] = home_dir
    result["status"] = "provisioned"

    log.info("Tenant %s fully provisioned", slug)
    return result
