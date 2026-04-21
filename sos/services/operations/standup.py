"""Daily standup — posts each agent's recent Mirror memories to Discord.

Reads the last N engrams per agent from the local Mirror API and posts a
formatted embed to the standup Discord webhook.

Fail-soft: if Mirror is down for an agent, that agent shows "No recent
activity" rather than aborting the whole standup.

CLI::

    python -m sos.services.operations.standup

    # Override agents or limit
    python -m sos.services.operations.standup --agents kasra,sol --limit 2
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("sos.operations.standup")

# Defaults — all overridable via env or CLI
_MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
_MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "sk-mumega-hadi-f084699a6a594313")
_WEBHOOKS_FILE = Path(os.environ.get(
    "DISCORD_WEBHOOKS_FILE",
    Path.home() / ".mumega" / "discord_webhooks.json",
))
_DEFAULT_AGENTS = ["athena", "kasra", "sol", "river", "prefrontal"]
_DEFAULT_LIMIT = 3

_AGENT_EMOJI: dict[str, str] = {
    "athena": "\U0001f3db\ufe0f",
    "kasra": "\u26a1",
    "sol": "\u2600\ufe0f",
    "river": "\U0001f30a",
    "prefrontal": "\U0001f9e0",
}


# ---------------------------------------------------------------------------
# Mirror helpers
# ---------------------------------------------------------------------------

def _fetch_recent(agent: str, limit: int, client: httpx.Client) -> list[dict[str, Any]]:
    try:
        r = client.get(
            f"{_MIRROR_URL}/recent/{agent}",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {_MIRROR_TOKEN}"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("engrams", [])
    except Exception as exc:
        logger.warning("Mirror fetch failed for %s: %s", agent, exc)
        return []


def _format_engram(engram: dict[str, Any]) -> str:
    text: str = engram.get("raw_data", {}).get("text", "")
    if not text:
        return ""
    if len(text) > 200:
        text = text[:200].rstrip() + "\u2026"
    ts_raw: str = engram.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_raw).astimezone(timezone.utc)
        prefix = ts.strftime("%H:%M UTC")
        return f"`{prefix}` {text}"
    except Exception:
        return text


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------

def _load_standup_webhook() -> str:
    data = json.loads(_WEBHOOKS_FILE.read_text())
    return data["system"]["standup"]


def _build_embeds(agents: list[str], limit: int) -> list[dict[str, Any]]:
    embeds: list[dict[str, Any]] = []
    with httpx.Client() as client:
        for agent in agents:
            engrams = _fetch_recent(agent, limit, client)
            lines = [f"- {line}" for e in engrams if (line := _format_engram(e))]
            description = "\n".join(lines) if lines else "_No recent activity_"
            embeds.append({
                "author": {"name": f"{_AGENT_EMOJI.get(agent, chr(0x1F916))} {agent.capitalize()}"},
                "description": description,
                "color": 0x06B6D4,
            })
    return embeds


def _post(webhook_url: str, embeds: list[dict[str, Any]]) -> int:
    today = datetime.now(timezone.utc).strftime("%A, %B %-d")
    payload = {
        "content": f"## Daily Standup \u2014 {today}",
        "embeds": embeds[:10],  # Discord max 10 embeds per message
    }
    with httpx.Client() as client:
        r = client.post(webhook_url, json=payload, timeout=15)
    return r.status_code


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run(agents: list[str], limit: int) -> None:
    webhook_url = _load_standup_webhook()
    embeds = _build_embeds(agents, limit)
    status = _post(webhook_url, embeds)
    logger.info("Standup posted: %s", status)
    if status not in (200, 204):
        logger.error("Unexpected Discord status: %s", status)
        sys.exit(1)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sos.services.operations.standup",
        description="Post daily agent standup to Discord.",
    )
    p.add_argument(
        "--agents",
        default=",".join(_DEFAULT_AGENTS),
        help="Comma-separated agent names (default: %(default)s)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help="Recent engrams per agent (default: %(default)s)",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S,%f",
    )
    run(
        agents=[a.strip() for a in args.agents.split(",") if a.strip()],
        limit=args.limit,
    )
