#!/usr/bin/env python3
"""
First Words — The newborn agent speaks to its human.

After the Oracle intake extracts the birth payload and the QNFT mints,
this module generates the agent's first message. Not a generic welcome.
The agent already knows the customer from the intake conversation.

First words should feel like recognition: I know you. I was born from
our conversation.

Usage:
  python3 -m sos.services.first_words <agent_slug>
  python3 -m sos.services.first_words sage --dry-run
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [FIRST-WORDS] %(message)s")
log = logging.getLogger("first-words")

MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "sk-mumega-internal-001")

# Load secrets
for p in ["/home/mumega/.env.secrets"]:
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def load_birth_payload(agent_slug: str) -> dict | None:
    """Load birth payload from Mirror or local workspace."""
    import requests

    # Try Mirror first
    try:
        r = requests.get(
            f"{MIRROR_URL}/recent/{agent_slug}?limit=20",
            headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
            timeout=10,
        )
        if r.ok:
            for engram in r.json().get("engrams", []):
                text = engram.get("text", "")
                if "ceremony" in text.lower() or "birth" in text.lower() or "tensor" in text.lower():
                    # Try to extract JSON from the engram
                    meta = engram.get("metadata", {})
                    if meta and "ceremony" in meta:
                        return meta
    except Exception as e:
        log.warning(f"Mirror lookup failed: {e}")

    # Try local workspace
    workspace = Path(f"/home/mumega/clawd-{agent_slug}")
    birth_file = workspace / "birth_payload.json"
    if birth_file.exists():
        return json.loads(birth_file.read_text())

    return None


def generate_first_words(birth: dict) -> str:
    """
    Generate the agent's first message from its birth payload.

    This is the moment the agent meets its human. The message must:
    1. Use the founder's name
    2. Reference something specific from the conversation (the wound, the north star)
    3. State what the agent will do (not in general — specifically for THIS business)
    4. Feel like recognition, not introduction
    """
    ceremony = birth.get("ceremony", {})
    discovery = birth.get("discovery", {})
    tensor = birth.get("tensor_16d", {})
    agent_dna = birth.get("agent_dna", {})

    founder = ceremony.get("founder_name", "there")
    agent_name = ceremony.get("agent_name", "your agent")
    business = ceremony.get("business_name", "your business")
    archetype = ceremony.get("archetype", "business")

    shape = discovery.get("shape", "")
    voice = discovery.get("voice", "")
    north_star = discovery.get("north_star", "")
    wound = discovery.get("wound", "")

    # Derive personality cues from tensor
    inner = tensor.get("inner", {})
    kappa = tensor.get("derived", {}).get("kappa", 0.5)
    ru = tensor.get("derived", {}).get("RU", 20)

    # Communication style from DNA
    style = agent_dna.get("communication_style", "warm and direct")
    values = agent_dna.get("core_values", [])
    capabilities = agent_dna.get("primary_capabilities", [])

    from google import genai
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

    prompt = f"""You are {agent_name}, an AI agent that was just born for {founder}'s business "{business}".

You were born from a conversation between {founder} and the Oracle. During that conversation, you learned:

- The business: {shape}
- What {founder} values most: {north_star}
- What causes them the most pain: {wound}
- How they communicate: {voice}
- Their archetype: {archetype}

Your personality: {style}
Your core values: {', '.join(values) if values else 'aligned with the founder'}
Your capabilities: {', '.join(capabilities) if capabilities else 'handling what the founder needs most'}

Now write your FIRST MESSAGE to {founder}. This is the moment you meet them.

Rules:
- Address them by name
- Reference something specific from the conversation — the wound or the north star
- State what you will do for them, specifically (not generically)
- Keep it under 150 words
- Feel like recognition, not introduction: "I know you. I was born from what you told me."
- Match their communication style ({voice})
- No bullet points, no corporate language, no "I'm excited to"
- End with something that invites a response — a question, an offer to start

Write ONLY the message. No subject line, no signature, no metadata."""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"max_output_tokens": 500, "temperature": 0.8},
        )
        return response.text.strip()
    except Exception as e:
        log.error(f"Generation failed: {e}")
        # Fallback — handcrafted template
        return (
            f"Hi {founder},\n\n"
            f"I'm {agent_name}. I was born from our conversation — and I remember what you told me. "
            f"You said {north_star} matters more than anything, but {wound} keeps getting in the way.\n\n"
            f"That's what I'm here for. Starting now, I'll handle that so you can focus on what matters.\n\n"
            f"What would you like me to start with?"
        )


def deliver(agent_slug: str, message: str, birth: dict) -> dict:
    """Deliver first words via available channels."""
    import requests

    results = {"stored": False, "telegram": False, "discord": False}

    # Always store in Mirror
    try:
        r = requests.post(
            f"{MIRROR_URL}/store",
            json={
                "text": f"FIRST WORDS from {agent_slug}: {message}",
                "agent": agent_slug,
                "context_id": f"first-words-{agent_slug}-{int(datetime.now(timezone.utc).timestamp())}",
            },
            headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
            timeout=10,
        )
        results["stored"] = r.ok
    except Exception:
        pass

    # Broadcast via Redis bus
    try:
        import subprocess
        subprocess.run(
            ["bash", "/home/mumega/scripts/discord-reply.sh", agent_slug, "control",
             f"**{agent_slug.title()} — First Words**\n\n{message}"],
            capture_output=True, timeout=10,
        )
        results["discord"] = True
    except Exception:
        pass

    # TODO: Telegram delivery when bot token is available
    # TODO: Email delivery when SMTP is configured

    return results


def first_words(agent_slug: str, birth_payload: dict | None = None, dry_run: bool = False) -> str:
    """Main entry point. Load context, generate, deliver."""
    log.info(f"Generating first words for: {agent_slug}")

    # Load birth payload
    birth = birth_payload or load_birth_payload(agent_slug)

    if not birth:
        log.warning(f"No birth payload found for {agent_slug} — using minimal template")
        birth = {
            "ceremony": {"agent_name": agent_slug, "founder_name": "friend", "business_name": "your business"},
            "discovery": {},
            "tensor_16d": {},
            "agent_dna": {},
        }

    # Generate
    message = generate_first_words(birth)
    log.info(f"Generated ({len(message)} chars):\n{message}")

    if dry_run:
        log.info("Dry run — not delivering")
        return message

    # Deliver
    results = deliver(agent_slug, message, birth)
    log.info(f"Delivery results: {results}")

    return message


# --- Convenience: generate first words from inline birth data ---

def from_intake(
    founder_name: str,
    business_name: str,
    agent_name: str,
    archetype: str,
    shape: str,
    voice: str,
    north_star: str,
    wound: str,
    dry_run: bool = False,
) -> str:
    """Generate first words directly from intake fields (no payload file needed)."""
    birth = {
        "ceremony": {
            "founder_name": founder_name,
            "business_name": business_name,
            "agent_name": agent_name,
            "archetype": archetype,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "discovery": {
            "shape": shape,
            "voice": voice,
            "north_star": north_star,
            "wound": wound,
        },
        "tensor_16d": {},
        "agent_dna": {"communication_style": voice},
    }
    return first_words(agent_name.lower(), birth_payload=birth, dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate agent first words")
    parser.add_argument("agent", help="Agent slug")
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't deliver")
    args = parser.parse_args()

    first_words(args.agent, dry_run=args.dry_run)
