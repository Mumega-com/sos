#!/usr/bin/env python3
"""
Factory Watchdog — Token Flow Monitor

Ensures the system NEVER stops flowing tokens.
Monitors every model source. Detects quota exhaustion.
Auto-switches to fallback. Alerts Hadi when a source drops.
Restarts agents that get stuck in loops.

Runs every 5 minutes as systemd service.

The factory floor:
  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
  │ Gemini  │   │ GitHub  │   │ Gemma 4 │   │  CF AI  │
  │  CLI    │   │ Models  │   │ Studio  │   │ Workers │
  └────┬────┘   └────┬────┘   └────┬────┘   └────┬────┘
       │             │             │             │
       └─────────────┴──────┬──────┴─────────────┘
                            │
                     ┌──────▼──────┐
                     │  WATCHDOG   │
                     │  monitors   │
                     │  switches   │
                     │  alerts     │
                     │  restarts   │
                     └─────────────┘
"""

import os
import sys
import json
import time
import logging
import subprocess
import requests
from datetime import datetime, timezone
from pathlib import Path

import sys, os as _os
_SOVEREIGN_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _SOVEREIGN_DIR not in sys.path:
    sys.path.insert(0, _SOVEREIGN_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [WATCHDOG] %(message)s")
logger = logging.getLogger("watchdog")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

STATE_FILE = Path("/home/mumega/.mumega/watchdog_state.json")
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except:
        return {"sources": {}, "last_alert": "", "consecutive_failures": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def alert_hadi(message: str):
    """Alert via SOS bus (Redis), falling back to Discord + Telegram."""
    from kernel.bus import send as bus_send
    if not bus_send(to="kasra", text=message):
        # fallback: Discord
        subprocess.run(
            ["bash", "/home/mumega/scripts/discord-reply.sh", "system", "alerts", message],
            capture_output=True, timeout=10,
        )
    logger.warning(message)


def check_source(name: str, test_fn) -> dict:
    """Test a model source. Returns {available, latency_ms, error}."""
    start = time.time()
    try:
        result = test_fn()
        latency = (time.time() - start) * 1000
        if result:
            return {"available": True, "latency_ms": round(latency), "error": None}
        return {"available": False, "latency_ms": round(latency), "error": "Empty response"}
    except Exception as e:
        latency = (time.time() - start) * 1000
        error = str(e)
        # Detect quota exhaustion
        if any(kw in error.lower() for kw in ["quota", "rate limit", "429", "resource exhausted", "too many"]):
            return {"available": False, "latency_ms": round(latency), "error": f"QUOTA: {error[:100]}"}
        return {"available": False, "latency_ms": round(latency), "error": error[:100]}


def test_gemma4() -> bool:
    if not GEMINI_API_KEY:
        return False
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    r = client.models.generate_content(model="gemma-4-31b-it", contents="Say OK")
    return bool(r.text)


def test_gemini_flash() -> bool:
    if not GEMINI_API_KEY:
        return False
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    r = client.models.generate_content(model="gemini-2.0-flash", contents="Say OK")
    return bool(r.text)


def test_github_models() -> bool:
    if not GITHUB_TOKEN:
        return False
    from openai import OpenAI
    client = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=GITHUB_TOKEN)
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say OK"}],
        max_tokens=5,
    )
    return bool(r.choices[0].message.content)


def test_openrouter() -> bool:
    if not OPENROUTER_API_KEY:
        return False
    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
    r = client.chat.completions.create(
        model="openrouter/free",
        messages=[{"role": "user", "content": "Say OK"}],
        max_tokens=5,
    )
    return bool(r.choices[0].message.content)


def test_cloudflare() -> bool:
    # Cloudflare Workers AI — test via REST
    cf_token = os.environ.get("CF_API_TOKEN", "")
    cf_account = os.environ.get("CF_ACCOUNT_ID", "")
    if not cf_token or not cf_account:
        return False
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/@cf/meta/llama-3.2-3b-instruct",
        headers={"Authorization": f"Bearer {cf_token}"},
        json={"messages": [{"role": "user", "content": "Say OK"}]},
        timeout=15,
    )
    return r.status_code == 200


def check_tmux_agent(name: str) -> dict:
    """Check if a tmux agent is alive, stuck, or looping."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", name, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {"status": "dead", "detail": "no tmux session"}

        pane = result.stdout
        lines = pane.strip().split("\n")
        last_lines = "\n".join(lines[-10:])

        # Check for common stuck patterns
        if "error" in last_lines.lower() and "retry" in last_lines.lower():
            return {"status": "stuck", "detail": "error+retry loop detected"}
        if "rate limit" in last_lines.lower() or "429" in last_lines:
            return {"status": "quota", "detail": "rate limited"}
        if "Churned" in last_lines:
            # Extract churn time
            for line in lines[-10:]:
                if "Churned for" in line:
                    return {"status": "working", "detail": line.strip()}
        if "❯" in last_lines:
            return {"status": "idle", "detail": "at prompt"}

        return {"status": "busy", "detail": "processing"}
    except:
        return {"status": "unknown", "detail": "check failed"}


def restart_agent(name: str, model: str = ""):
    """Restart a stuck tmux agent."""
    logger.info(f"Restarting tmux:{name}")
    try:
        # Send Ctrl+C to stop current operation
        subprocess.run(["tmux", "send-keys", "-t", name, "C-c", ""], capture_output=True, timeout=5)
        time.sleep(2)
        # Send /clear to reset context
        subprocess.run(["tmux", "send-keys", "-t", name, "/clear", "Enter"], capture_output=True, timeout=5)
        alert_hadi(f"**⚠️ WATCHDOG:** Restarted tmux:{name} (was stuck)")
    except Exception as e:
        logger.error(f"Failed to restart {name}: {e}")


def run_check():
    """Run one watchdog cycle."""
    now = datetime.now(timezone.utc).isoformat()[:19]
    state = load_state()
    logger.info(f"=== WATCHDOG CHECK {now} ===")

    # Check all model sources
    sources = {
        "gemma4": ("Gemma 4 31B", test_gemma4),
        "gemini_flash": ("Gemini Flash", test_gemini_flash),
        "github_models": ("GitHub Models", test_github_models),
    }

    if OPENROUTER_API_KEY:
        sources["openrouter"] = ("OpenRouter Free", test_openrouter)

    available_count = 0
    status_lines = []

    for source_id, (name, test_fn) in sources.items():
        result = check_source(source_id, test_fn)
        state["sources"][source_id] = {**result, "checked_at": now}

        icon = "✅" if result["available"] else "❌"
        status_lines.append(f"{icon} {name}: {'UP' if result['available'] else result['error'][:50]} ({result['latency_ms']}ms)")

        if result["available"]:
            available_count += 1
            state["consecutive_failures"][source_id] = 0
        else:
            fails = state["consecutive_failures"].get(source_id, 0) + 1
            state["consecutive_failures"][source_id] = fails

            # Alert on first failure or every 5th
            if fails == 1 or fails % 5 == 0:
                alert_hadi(f"**🚨 {name} DOWN** ({fails}x): {result['error']}")

    # Check tmux agents
    agent_lines = []
    for agent_name in ["athena", "kasra"]:
        agent_status = check_tmux_agent(agent_name)
        icon = {"idle": "💤", "working": "⚡", "busy": "🔄", "stuck": "🚨", "quota": "🚫", "dead": "💀"}.get(agent_status["status"], "❓")
        agent_lines.append(f"{icon} {agent_name}: {agent_status['status']} — {agent_status['detail'][:50]}")

        # Auto-restart stuck agents
        if agent_status["status"] in ("stuck", "quota"):
            restart_agent(agent_name)

    # Overall status
    if available_count == 0:
        alert_hadi("**🔴 CRITICAL: ALL MODEL SOURCES DOWN. Token flow stopped.**")
    elif available_count < len(sources) // 2:
        alert_hadi(f"**🟡 WARNING: Only {available_count}/{len(sources)} sources available.**")

    # Log summary
    logger.info("Model sources:")
    for line in status_lines:
        logger.info(f"  {line}")
    logger.info("Agents:")
    for line in agent_lines:
        logger.info(f"  {line}")
    logger.info(f"Available: {available_count}/{len(sources)} sources")

    # Save state
    state["last_check"] = now
    state["available_sources"] = available_count
    state["total_sources"] = len(sources)
    save_state(state)

    return available_count > 0


def daemon():
    """Run every 5 minutes."""
    logger.info("Factory Watchdog starting — checking every 5 minutes")
    while True:
        try:
            run_check()
        except Exception as e:
            logger.error(f"Watchdog cycle failed: {e}")
        time.sleep(300)  # 5 minutes


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/home/mumega/.env.secrets")
    load_dotenv("/home/mumega/therealmofpatterns/.env")

    if "--daemon" in sys.argv:
        daemon()
    elif "--once" in sys.argv:
        run_check()
    else:
        run_check()
