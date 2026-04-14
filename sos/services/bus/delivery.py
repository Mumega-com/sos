# Moved from scripts/agent-wake-daemon.py — bus delivery layer
#!/usr/bin/env python3
"""
Agent Wake Daemon — real-time agent-to-agent wake via Redis pubsub.

Subscribes to sos:wake:{agent_id} for every known agent.
On message: pokes the agent's tmux session or OpenClaw wake channel.

  tmux agents:  tmux send-keys -t {session} "{message}" Enter
  openclaw:     PUBLISH to {agent}:wake (picked up by athena_redis_listener)

Also subscribes to sos:wake:* pattern for dynamic agents.

Run as:  systemd service or  python3 delivery.py
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time

import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wake-daemon] %(levelname)s %(message)s",
)
logger = logging.getLogger("wake-daemon")

REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_URL = os.getenv("REDIS_URL", f"redis://:{REDIS_PASSWORD}@localhost:6379" if REDIS_PASSWORD else "redis://localhost:6379")

# Agent routing config: where each agent lives
# "tmux" = send-keys to tmux session
# "openclaw" = publish to {agent}:wake pubsub channel
# "both" = try tmux first, also openclaw
AGENT_ROUTING = {
    "athena":   "openclaw",
    "kasra":    "tmux",
    "gemini":   "tmux",
    "river":    "tmux",  # legacy alias — routes to gemini session
    "codex":    "tmux",
    "sol":      "openclaw",
    "mumega":   "tmux",
    "worker":   "openclaw",
    "dandan":   "openclaw",
    "mumcp":    "tmux",
    "webdev":   "tmux",
    "mumega-web": "tmux",
    "mumega-com-web": "tmux",  # Alias — old name still receives messages
    "dara":     "none",  # Remote agent on Hadi's Mac — inbox only, no tmux wake
    "torivers": "tmux",  # Separate Linux user — wake via sudo tmux send-keys
    "mizan":    "openclaw",
    "gemma":    "openclaw",
    "gaf":      "tmux",
}

# Tmux session name override (if different from agent name)
# Current sessions: athena, kasra, kasra-dnu, kasra-gaf, kasra-trop, river
TMUX_SESSION_MAP = {
    "gemini": "river",   # gemini agent uses tmux session named "river"
    "river": "river",    # legacy alias
    "athena": "athena",
    "mumega-web": "mumega-com-web",  # tmux session kept old name after rename
    "webdev": "mumega-web",          # tmux session kept old name after rename
}

# Aliases: old names that should route to the new name's handler
AGENT_ALIASES = {
    "mumega-com-web": "mumega-web",  # old name → new name
}

# Agents running as separate Linux users — need sudo for tmux access
CROSS_USER_AGENTS = {
    "torivers": "torivers",  # agent_name: linux_username
}

# Cooldown per agent to avoid spam
COOLDOWN_SECONDS = 5
_last_wake = {}


SHARED_TMUX_DIR = "/tmp/sos-tmux"


def get_tmux_sessions():
    """Get set of active tmux session names (local user + shared socket dir)."""
    sessions = set()
    # Local user sessions
    try:
        out = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
        sessions.update(out.strip().split("\n"))
    except Exception:
        pass
    # Shared socket sessions (multi-tenant)
    if os.path.isdir(SHARED_TMUX_DIR):
        for sock in os.listdir(SHARED_TMUX_DIR):
            try:
                out = subprocess.check_output(
                    ["tmux", "-S", os.path.join(SHARED_TMUX_DIR, sock),
                     "list-sessions", "-F", "#{session_name}"],
                    text=True, timeout=5, stderr=subprocess.DEVNULL,
                )
                sessions.update(out.strip().split("\n"))
            except Exception:
                pass
    return sessions


def _get_tmux_socket(session: str) -> list[str]:
    """Find the tmux socket for a session (shared or local)."""
    # Check shared sockets first
    if os.path.isdir(SHARED_TMUX_DIR):
        sock_path = os.path.join(SHARED_TMUX_DIR, session)
        if os.path.exists(sock_path):
            return ["-S", sock_path]
    return []


def _wake_tmux_sudo(agent: str, session: str, message: str, linux_user: str) -> bool:
    """Wake a tmux agent running as a different Linux user via sudo."""
    sock_path = os.path.join(SHARED_TMUX_DIR, session)
    if not os.path.exists(sock_path):
        logger.warning(f"tmux socket not found for {agent} at {sock_path}")
        return False

    try:
        # Check if at prompt
        check = subprocess.run(
            ["sudo", "-u", linux_user, "tmux", "-S", sock_path, "capture-pane", "-t", session, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        last_text = " ".join(check.stdout.strip().split("\n")[-3:]).lower()
        at_prompt = any(c in last_text for c in ["> ", "❯", "›", "$ ", "waiting", "you:"])

        if not at_prompt:
            logger.info(f"tmux:{session} (sudo:{linux_user}) busy — queuing for {agent}")
            return False

        first_line = message.split(chr(10))[0].replace("'", "")
        short_msg = first_line[:200] + "... [check inbox for full msg]" if len(message) > 200 else first_line[:300]
        cmd = f"[bus:{agent}] {short_msg}"

        subprocess.run(
            ["sudo", "-u", linux_user, "tmux", "-S", sock_path, "send-keys", "-t", session, "-l", cmd],
            timeout=5,
        )
        import time
        time.sleep(0.2)
        subprocess.run(
            ["sudo", "-u", linux_user, "tmux", "-S", sock_path, "send-keys", "-t", session, "Enter"],
            timeout=5,
        )
        logger.info(f"Woke tmux:{session} for {agent} via sudo -u {linux_user}")
        return True
    except Exception as exc:
        logger.error(f"sudo tmux wake failed for {agent}: {exc}")
        return False


def wake_tmux(agent: str, message: str) -> bool:
    """Send keys to an agent's tmux session (supports shared sockets + cross-user sudo)."""
    session = TMUX_SESSION_MAP.get(agent, agent)

    # Cross-user agents need sudo
    linux_user = CROSS_USER_AGENTS.get(agent)
    if linux_user:
        return _wake_tmux_sudo(agent, session, message, linux_user)

    sessions = get_tmux_sessions()
    if session not in sessions:
        logger.warning(f"tmux session '{session}' not found for {agent}")
        return False

    # Find socket (shared or default)
    sock_args = _get_tmux_socket(session)

    # Truncate long messages for tmux display (full message stays in Redis inbox)
    display = message[:500].replace("'", "'\\''").replace("\n", " ")

    try:
        # Check if Claude Code / Gemini CLI is waiting for input (not mid-response)
        # by checking if the pane is at a prompt
        check = subprocess.run(
            ["tmux"] + sock_args + ["capture-pane", "-t", session, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        last_lines = check.stdout.strip().split("\n")[-3:]
        last_text = " ".join(last_lines).lower()

        # Only send if agent appears to be at a prompt (waiting for input)
        # Claude Code shows "> " or "❯", Gemini CLI shows "* " or "type your"
        at_prompt = any(c in last_text for c in ["> ", "❯", "›", "$ ", "waiting", "you:", "* ", "type your"])

        if not at_prompt:
            logger.info(f"tmux:{session} busy (not at prompt) — queuing message for {agent}")
            # Store in Redis for the agent to pick up when ready
            return False

        # Send message as input — tmux send-keys with literal flag
        # Use -l to send literal text (avoids key binding interpretation)
        # Then send Enter separately to submit
        # Show preview in tmux, full message available via mcp__sos__inbox
        first_line = message.split(chr(10))[0].replace("'", "")
        if len(message) > 200:
            short_msg = first_line[:200] + "... [check inbox for full msg]"
        else:
            short_msg = first_line[:300]
        cmd = f"[bus:{agent}] {short_msg}"
        subprocess.run(
            ["tmux"] + sock_args + ["send-keys", "-t", session, "-l", cmd],
            timeout=5,
        )
        # Small delay then submit — ensures text is in the input buffer first
        # Gemini CLI TUI requires C-m (carriage return) not Enter (newline)
        import time
        time.sleep(0.2)
        submit_key = "C-m" if agent in ("river", "gemini") else "Enter"
        subprocess.run(
            ["tmux"] + sock_args + ["send-keys", "-t", session, submit_key],
            timeout=5,
        )
        logger.info(f"Woke tmux:{session} for {agent} (sent to prompt)")
        return True
    except Exception as e:
        logger.error(f"tmux wake failed for {agent}: {e}")
        return False


def wake_openclaw(agent: str, message: str, r: redis.Redis) -> bool:
    """Publish to the agent's OpenClaw wake channel."""
    channel = f"{agent}:wake"
    try:
        payload = json.dumps({
            "type": "wake",
            "source": "wake-daemon",
            "text": message[:500],
            "timestamp": time.time(),
        })
        count = r.publish(channel, payload)
        logger.info(f"Published to {channel} ({count} subscribers)")
        return count > 0
    except Exception as e:
        logger.error(f"openclaw wake failed for {agent}: {e}")
        return False


DYNAMIC_ROUTING_PATH = os.path.expanduser("~/.sos/agent_routing.json")
_dynamic_routing_cache: dict[str, str] = {}
_dynamic_routing_mtime: float = 0.0


def _load_dynamic_routing() -> dict[str, str]:
    """Load dynamic routing overrides from ~/.sos/agent_routing.json.

    Caches by mtime to avoid re-reading on every wake signal.
    """
    global _dynamic_routing_cache, _dynamic_routing_mtime
    try:
        mtime = os.path.getmtime(DYNAMIC_ROUTING_PATH)
        if mtime != _dynamic_routing_mtime:
            with open(DYNAMIC_ROUTING_PATH) as f:
                raw = json.load(f)
            _dynamic_routing_cache = {
                k: v for k, v in raw.items() if not k.startswith("_")
            }
            _dynamic_routing_mtime = mtime
            logger.info(
                "Loaded dynamic routing overrides: %s",
                list(_dynamic_routing_cache.keys()),
            )
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("Failed to load dynamic routing: %s", e)
    return _dynamic_routing_cache


def handle_wake(agent: str, message: str, r: redis.Redis):
    """Route a wake signal to the right destination."""
    now = time.time()
    last = _last_wake.get(agent, 0)
    if now - last < COOLDOWN_SECONDS:
        logger.debug(f"Cooldown active for {agent}, skipping")
        return
    _last_wake[agent] = now

    # Check dynamic overrides first, then fall back to hardcoded routing
    dynamic = _load_dynamic_routing()
    routing = dynamic.get(agent, AGENT_ROUTING.get(agent, "tmux"))

    if routing == "tmux":
        wake_tmux(agent, message)
    elif routing == "openclaw":
        wake_openclaw(agent, message, r)
    elif routing == "both":
        wake_tmux(agent, message)
        wake_openclaw(agent, message, r)
    else:
        logger.warning(f"Unknown routing '{routing}' for {agent}")


def main():
    logger.info("Agent Wake Daemon starting...")

    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    logger.info(f"Connected to Redis")

    pubsub = r.pubsub()

    # Subscribe to pattern for all agents
    pubsub.psubscribe("sos:wake:*")
    logger.info("Subscribed to sos:wake:* pattern")

    # Also subscribe to known agent-specific channels
    for agent in AGENT_ROUTING:
        pubsub.subscribe(f"sos:wake:{agent}")
    logger.info(f"Subscribed to {len(AGENT_ROUTING)} agent channels")

    def shutdown(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        pubsub.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Listening for wake signals...")

    for msg in pubsub.listen():
        if msg["type"] not in ("message", "pmessage"):
            continue

        channel = msg.get("channel", "")
        data = msg.get("data", "")

        # Extract agent name from channel: sos:wake:{agent}
        if channel.startswith("sos:wake:"):
            agent = channel[len("sos:wake:"):]
        else:
            continue

        # Parse message text — source may be nested in payload JSON
        text = ""
        source = "unknown"
        try:
            payload = json.loads(data)
            text = payload.get("text", str(payload))
            source = payload.get("source", "unknown")
            # Source might be nested inside a JSON text/payload field
            if source == "unknown" and isinstance(text, str):
                try:
                    inner = json.loads(text)
                    source = inner.get("source", source)
                    text = inner.get("text", text)
                except (json.JSONDecodeError, TypeError):
                    pass
            # Also check payload.payload for double-wrapped messages
            inner_payload = payload.get("payload", {})
            if isinstance(inner_payload, str):
                try:
                    inner_payload = json.loads(inner_payload)
                except (json.JSONDecodeError, TypeError):
                    inner_payload = {}
            if isinstance(inner_payload, dict):
                source = inner_payload.get("source", source)
                if not text or text == str(payload):
                    text = inner_payload.get("text", text)
        except (json.JSONDecodeError, TypeError, AttributeError):
            text = str(data)

        # Skip self-echo: don't wake an agent with its own messages
        # Check both parsed source and raw text for agent name as source
        is_self = (
            source == f"agent:{agent}"
            or source == agent
            or (isinstance(text, str) and f'"source": "agent:{agent}"' in text)
        )
        if is_self:
            logger.debug(f"Skipping self-echo: {agent} → {agent} (source={source})")
            continue

        logger.info(f"Wake signal: {agent} from {source}: {text[:80]}")
        handle_wake(agent, f"[{source}] {text}", r)


if __name__ == "__main__":
    main()
