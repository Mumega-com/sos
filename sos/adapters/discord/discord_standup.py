#!/usr/bin/env python3
"""
Daily standup — posts each agent's recent Mirror memories to Discord.
Cron: 0 13 * * * (1pm server time)
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [standup] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S,%f",
)
logger = logging.getLogger("standup")

MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "sk-mumega-hadi-f084699a6a594313")
WEBHOOKS_FILE = os.path.expanduser("~/.mumega/discord_webhooks.json")

AGENTS = ["athena", "kasra", "sol", "river", "prefrontal"]
AGENT_EMOJI = {
    "athena": "\U0001f3db\ufe0f",
    "kasra": "\u26a1",
    "sol": "\u2600\ufe0f",
    "river": "\U0001f30a",
    "prefrontal": "\U0001f9e0",
}
MEMORY_LIMIT = 3


def load_webhooks():
    with open(WEBHOOKS_FILE) as f:
        return json.load(f)


def fetch_recent(agent, client):
    try:
        r = client.get(
            f"{MIRROR_URL}/recent/{agent}",
            params={"limit": MEMORY_LIMIT},
            headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("engrams", [])
    except Exception as e:
        logger.warning(f"Mirror fetch failed for {agent}: {e}")
        return []


def format_engram(engram):
    text = engram.get("raw_data", {}).get("text", "")
    if not text:
        return ""
    if len(text) > 200:
        text = text[:200].rstrip() + "\u2026"
    ts_raw = engram.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_raw).astimezone(timezone.utc)
        ts_str = ts.strftime("%H:%M UTC")
    except Exception:
        ts_str = ""
    return f"`{ts_str}` {text}" if ts_str else text


def build_embeds():
    embeds = []
    with httpx.Client() as client:
        for agent in AGENTS:
            emoji = AGENT_EMOJI.get(agent, "\U0001f916")
            engrams = fetch_recent(agent, client)
            if engrams:
                lines = [f"- {format_engram(e)}" for e in engrams if format_engram(e)]
                description = "\n".join(lines) or "_No recent activity_"
            else:
                description = "_No recent activity_"
            embeds.append({
                "author": {"name": f"{emoji} {agent.capitalize()}"},
                "description": description,
                "color": 0x06B6D4,
            })
    return embeds


def post_standup(webhook_url, embeds):
    today = datetime.now(timezone.utc).strftime("%A, %B %-d")
    payload = {
        "content": f"## Daily Standup \u2014 {today}",
        "embeds": embeds[:10],
    }
    with httpx.Client() as client:
        r = client.post(webhook_url, json=payload, timeout=15)
    return r.status_code


def main():
    webhooks = load_webhooks()
    standup_url = webhooks["system"]["standup"]
    embeds = build_embeds()
    status = post_standup(standup_url, embeds)
    logger.info(f"Standup posted: {status}")
    if status not in (200, 204):
        logger.error(f"Unexpected Discord status: {status}")
        sys.exit(1)


main()
