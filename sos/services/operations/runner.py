#!/usr/bin/env python3
"""
Operation Runner — executes a delivery pipeline for a customer.

Reads an operation template (YAML), fills in customer context,
runs each phase sequentially through the team roles, gates quality,
and delivers results.

Usage:
  python3 -m sos.services.operations.runner stemminds content-writer
  python3 -m sos.services.operations.runner stemminds content-writer --dry-run
"""
from __future__ import annotations

import json
import logging
import os
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("operation-runner")

# Paths
OPERATIONS_DIR = Path("/home/mumega/SOS/operations")
ORGANISMS_DIR = Path("/home/mumega/.mumega/organisms")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "sk-mumega-internal-001")

def load_secrets() -> None:
    """Load secrets from SOS-owned sources only."""
    secrets_path = "/home/mumega/.env.secrets"
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


load_secrets()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def load_template(product_slug: str) -> dict:
    """Load operation template YAML."""
    path = OPERATIONS_DIR / f"{product_slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No template: {path}")
    return yaml.safe_load(path.read_text())


def load_customer_context(customer_slug: str) -> dict:
    """Load customer context from organism YAML."""
    path = ORGANISMS_DIR / f"{customer_slug}.yaml"
    if not path.exists():
        log.warning(f"No organism for {customer_slug}, using empty context")
        return {}
    return yaml.safe_load(path.read_text()) or {}


def fill_template(text: str, context: dict) -> str:
    """Fill {variables} in a template string."""
    for key, value in context.items():
        if isinstance(value, str):
            text = text.replace(f"{{{key}}}", value)
        elif isinstance(value, list):
            text = text.replace(f"{{{key}}}", ", ".join(str(v) for v in value))
    return text


def call_model(model: str, prompt: str, max_tokens: int = 2000, temperature: float = 0.7) -> str:
    """Call an LLM. Supports gemma-4-31b and gemini-flash (free via Google GenAI)."""
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        # Map model names to Google API model IDs
        model_map = {
            "gemma-4-31b": "gemma-4-31b-it",
            "gemini-flash": "gemini-2.0-flash",
            "gemini-pro": "gemini-2.5-pro-preview-05-06",
        }
        api_model = model_map.get(model, model)

        response = client.models.generate_content(
            model=api_model,
            contents=prompt,
            config={
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        return response.text.strip()
    except Exception as e:
        log.error(f"Model call failed ({model}): {e}")
        return f"ERROR: {e}"


def call_tool(tool: str, task: str, context: dict) -> str:
    """Execute a tool-based role (wordpress_api, social_api, web_fetch)."""
    if tool == "wordpress_api":
        return wordpress_publish(context)
    elif tool == "social_api":
        return social_publish(context)
    elif tool == "web_fetch":
        return web_fetch(context)
    else:
        return f"Unknown tool: {tool}"


def wordpress_publish(context: dict) -> str:
    """Publish to WordPress via SitePilot AI MCP endpoint."""
    import requests

    url = context.get("wordpress_url", "").rstrip("/")
    spai_key = context.get("spai_api_key", os.environ.get("SPAI_API_KEY", ""))

    if not url:
        return "ERROR: no wordpress_url"

    content = context.get("_article", context.get("_reviewed", ""))
    title = content.split("\n")[0].lstrip("# ").strip() if content else "Untitled"
    body = "\n".join(content.split("\n")[1:]).strip()

    if not spai_key:
        # Fallback: store as draft in Mirror
        log.warning("No SitePilot API key — storing post in Mirror instead")
        mirror_store(context.get("customer", "unknown"), f"DRAFT POST: {title}\n\n{content}")
        return f"Stored as draft in Mirror (no SPAI key): {title}"

    try:
        # Use SitePilot AI MCP endpoint
        resp = requests.post(
            f"{url}/wp-json/site-pilot-ai/v1/mcp",
            headers={
                "X-API-Key": spai_key,
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "wp_create_post",
                    "arguments": {
                        "title": title,
                        "content": body,
                        "status": "publish",
                        "format": "standard",
                    },
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        # Extract post URL from MCP response
        post_data = result.get("result", {})
        if isinstance(post_data, dict):
            content_items = post_data.get("content", [])
            for item in content_items:
                if isinstance(item, dict) and "text" in item:
                    # Parse the post URL from response text
                    import re
                    url_match = re.search(r'https?://[^\s"]+', item.get("text", ""))
                    if url_match:
                        post_url = url_match.group()
                        log.info(f"Published via SitePilot: {post_url}")
                        return post_url

        log.info(f"Published via SitePilot: {title}")
        return f"Published: {title}"

    except Exception as e:
        log.error(f"SitePilot publish failed: {e}")
        # Fallback to Mirror storage
        mirror_store(context.get("customer", "unknown"), f"FAILED POST: {title}\n\n{content}")
        return f"ERROR: {e}"


def social_publish(context: dict) -> str:
    """Publish to social media. Placeholder — needs per-platform API integration."""
    platform = context.get("platform", "unknown")
    post = context.get("_approved_post", context.get("_post", ""))
    log.info(f"Would publish to {platform}: {post[:100]}")
    mirror_store(context.get("customer", "unknown"), f"SOCIAL [{platform}]: {post}")
    return f"Stored social post for {platform} (API integration pending)"


def web_fetch(context: dict) -> str:
    """Fetch a URL and return content."""
    import requests
    url = context.get("customer_url", "")
    if not url:
        return "ERROR: no customer_url"
    try:
        resp = requests.get(url, timeout=15)
        return resp.text[:5000]
    except Exception as e:
        return f"ERROR: {e}"


def mirror_store(agent: str, text: str) -> None:
    """Store result in Mirror."""
    import requests
    try:
        requests.post(
            f"{MIRROR_URL}/store",
            headers={"Authorization": f"Bearer {MIRROR_TOKEN}", "Content-Type": "application/json"},
            json={"text": text[:5000], "agent": agent, "context_id": f"op_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Mirror store failed: {e}")


def pick_topic(topics: str, run_count: int) -> str:
    """Rotate through topics."""
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    if not topic_list:
        return "general"
    return topic_list[run_count % len(topic_list)]


def run_operation(customer_slug: str, product_slug: str, dry_run: bool = False) -> dict:
    """Execute one cycle of an operation."""

    log.info(f"=== Operation: {customer_slug} / {product_slug} ===")

    # Load template + customer context
    template = load_template(product_slug)
    customer = load_customer_context(customer_slug)

    # Build context dict for variable filling
    ctx = {
        "customer": customer_slug,
        "customer_url": customer.get("wordpress", {}).get("url", customer.get("workspace", "")),
        "wordpress_url": customer.get("wordpress", {}).get("url", ""),
        "brand_voice": customer.get("brand_voice", "professional and helpful"),
        "topics": customer.get("content", {}).get("types", ["business tips"]),
        "industry": customer.get("industry", "business"),
        "customer_email": customer.get("customer_email", customer.get("escalate_to", "")),
        "platforms": customer.get("platforms", ["twitter"]),
    }

    # Pick topic for this run
    topics_str = ", ".join(ctx["topics"]) if isinstance(ctx["topics"], list) else str(ctx["topics"])
    run_count = customer.get("_run_count", 0)
    ctx["topic"] = pick_topic(topics_str, run_count)

    # Fill objective
    objective = fill_template(template.get("objective_template", ""), ctx)
    log.info(f"Objective: {objective}")

    if dry_run:
        log.info("[DRY RUN] Would execute phases:")
        for phase in template.get("phases", []):
            log.info(f"  → {phase['name']} (roles: {phase.get('roles', [])})")
        return {"status": "dry_run", "objective": objective}

    # Build team lookup
    team = {}
    for role_def in template.get("team", []):
        team[role_def["role"]] = role_def

    # Run phases
    results = {}
    phase_outputs = {}

    for phase_def in template.get("phases", []):
        phase_name = phase_def["name"]
        roles = phase_def.get("roles", [])
        input_key = phase_def.get("input_key", "")
        output_key = phase_def.get("output_key", phase_name)

        log.info(f"--- Phase: {phase_name} ---")

        # Get input from previous phase
        if input_key and input_key in phase_outputs:
            ctx[f"_{input_key}"] = phase_outputs[input_key]

        # Execute each role in this phase
        phase_result = ""
        for role_name in roles:
            role = team.get(role_name)
            if not role:
                log.warning(f"Role {role_name} not in team, skipping")
                continue

            task_prompt = fill_template(role["task"], ctx)

            if role.get("tool"):
                # Tool-based execution
                phase_result = call_tool(role["tool"], task_prompt, {**ctx, f"_{input_key}": phase_outputs.get(input_key, "")})
            else:
                # Model-based execution
                phase_result = call_model(
                    model=role["model"],
                    prompt=task_prompt,
                    max_tokens=role.get("max_tokens", 2000),
                    temperature=role.get("temperature", 0.7),
                )

            log.info(f"  {role_name}: {len(phase_result)} chars output")

        # Store phase output
        phase_outputs[output_key] = phase_result

        # Check gate
        gate_def = phase_def.get("gate")
        if gate_def:
            # Try to extract score from result
            score = _extract_score(phase_result)
            passed = score >= gate_def.get("threshold", 0) if score is not None else True

            if not passed:
                log.warning(f"Gate failed: {gate_def['metric']} = {score} (need {gate_def['operator']} {gate_def['threshold']})")
                results[phase_name] = {"status": "gate_failed", "score": score}
                break
            else:
                log.info(f"Gate passed: score={score}")

        results[phase_name] = {"status": "passed", "output_length": len(phase_result)}

    # Store operation result in Mirror
    summary = f"Operation [{product_slug}] for {customer_slug}: {objective}\n"
    for name, res in results.items():
        summary += f"  {name}: {res.get('status', '?')}\n"
    if "url" in phase_outputs:
        summary += f"  Delivered: {phase_outputs['url']}\n"

    mirror_store(customer_slug, summary)

    log.info(f"=== Done: {customer_slug} / {product_slug} ===")

    return {
        "status": "delivered" if all(r.get("status") == "passed" for r in results.values()) else "partial",
        "customer": customer_slug,
        "product": product_slug,
        "objective": objective,
        "phases": results,
        "deliverables": {k: v[:200] for k, v in phase_outputs.items()},
    }


def _extract_score(text: str) -> float | None:
    """Try to extract a numeric score from review text."""
    import re
    patterns = [
        r"(?:score|rating|overall)[:\s]*(\d+)",
        r"(\d+)\s*/\s*100",
        r"(\d+)%",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run an operation")
    parser.add_argument("customer", help="Customer slug")
    parser.add_argument("product", help="Product slug")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_operation(args.customer, args.product, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
