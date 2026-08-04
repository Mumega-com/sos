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
import time
from typing import Any, Callable

import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wake-daemon] %(levelname)s %(message)s",
)
logger = logging.getLogger("wake-daemon")

from sos.kernel.settings import get_settings as _get_settings
_redis_settings = _get_settings().redis
REDIS_PASSWORD = _redis_settings.password_str
REDIS_URL = _redis_settings.resolved_url

# Agent routing config: where each agent lives
# "tmux" = send-keys to tmux session
# "openclaw" = publish to {agent}:wake pubsub channel
# "both" = try tmux first, also openclaw
AGENT_ROUTING = {
    "athena":   "tmux",
    "kasra":    "tmux",
    "gemini":   "tmux",
    "river":    "tmux",  # legacy alias — routes to gemini session
    "codex":    "tmux",
    "sol":      "openclaw",
    "mumega":   "tmux",
    "worker":   "openclaw",
    "dandan":   "openclaw",
    "mumcp":    "tmux",
    # webdev / mumega-web / mumega-com-web: DEPRECATED 2026-04-16 (Hadi: obsolete).
    # Removed from routing so wake-daemon stops poking dead tmux sessions.
    "dara":     "none",  # Remote agent on Hadi's Mac — inbox only, no tmux wake
    "torivers": "tmux",  # Separate Linux user — wake via sudo tmux send-keys
    "mizan":    "tmux",     # Resurrected 2026-04-27 on Sonnet via Claude Code (Hadi directive). Business agent.
    "gemma":    "openclaw",
    "gaf":      "tmux",
    "prefrontal": "tmux",  # Customer agent — separate Linux user
    "trop":     "tmux",    # TROP growth loop agent — Sonnet
    "sos-medic": "tmux",   # SOS connectivity on-call responder (home: sos/agents/sos-medic/)
    "loom":     "tmux",    # SOS bus + task wiring (home: mumega.com/agents/loom/)
    "hermes":   "tmux",    # Ops + architecture review (home: mumega.com/agents/hermes/)
    "hadi-hermes": "none", # Remote Mac Hermes CLI — inbox/SSE only, no tmux wake
    "kaveh":    "tmux",    # First customer knight — GAF/SR&ED
    "lex-knight": "tmux",  # Lex Ace knight — PEI regional activator + partner pipeline
    "calliope": "tmux",    # Mumega content writer — Sonnet via Claude Code (home: mumega.com/agents/calliope/). Provisioned 2026-05-04.
}

# Tmux session name override (if different from agent name)
# Current sessions: athena, kasra, kasra-dnu, kasra-gaf, kasra-trop, river
TMUX_SESSION_MAP = {
    "gemini": "gemini",  # gemini CLI runs in tmux session "gemini"
    "river": "river",    # legacy alias
    "athena": "athena",
    # webdev / mumega-web / mumega-com-web entries removed 2026-04-16 (obsolete)
}

# Aliases: old names that should route to the new name's handler
AGENT_ALIASES: dict[str, str] = {
    # mumega-com-web → mumega-web alias removed 2026-04-16 (both deprecated)
}

# Agents running as separate Linux users — need sudo for tmux access
CROSS_USER_AGENTS = {
    "torivers": "torivers",  # agent_name: linux_username
}

# Cooldown per agent to avoid spam
COOLDOWN_SECONDS = 5
_last_wake = {}

# #594 — real deferred retry when pane is busy (Cursor mid-run / not at prompt).
# Prior code logged "queuing message" but only returned False — no Redis write, no retry.
DEFERRED_WAKE_ZSET = "sos:wake:deferred"
DEFERRED_WAKE_MAX_ATTEMPTS = 20
DEFERRED_WAKE_MAX_AGE_SECONDS = 15 * 60
DEFERRED_WAKE_BASE_DELAY_SECONDS = 15.0
DEFERRED_WAKE_MAX_DELAY_SECONDS = 120.0
DEFERRED_POLL_TIMEOUT_SECONDS = 1.0

# Prompt glyphs: stripped startswith match. Equality misses Codex/Claude prompts
# that carry placeholder or typed text on the same line ('› Summarize recent
# commits', '❯ deploy the thing'). Do not include '*' — a lone asterisk is a
# plausible bullet/diff marker in scrollback (Kasra HOLD on sos#211 / 076f853c).
_PROMPT_GLYPHS = (">", "❯", "›", "$")
# Substring markers for phrases / Cursor idle chrome (scan trailing window).
_PROMPT_SUBSTRING_MARKERS = (
    "waiting",
    "you:",
    "type your",
    "→ add a follow-up",  # Cursor idle input box (athena pane)
)
# Busy markers must be safe as substrings of the *last non-empty line only*.
# Never use "working" (matches wake template "WORKING DIR:") or " tokens"
# (matches idle Claude Code status chrome) — Kasra gate BLOCK on sos#211.
_CURSOR_BUSY_MARKERS = (
    "running…",
    "running...",
    "ctrl+c to stop",
)
_APPROVAL_MODAL_MARKERS = (
    "would you like to run the following command?",
    "press enter to confirm or esc to cancel",
    "yes, proceed",
    "don't ask again",
)


SHARED_TMUX_DIR = "/tmp/sos-tmux"


def busy_retry_delay_seconds(attempts: int) -> float:
    """Exponential backoff for busy panes, capped."""
    if attempts < 0:
        raise ValueError("attempts must be >= 0")
    delay = DEFERRED_WAKE_BASE_DELAY_SECONDS * (2 ** attempts)
    return min(delay, DEFERRED_WAKE_MAX_DELAY_SECONDS)


def last_nonempty_line(pane_text: str) -> str:
    """Status/prompt chrome lives on the latest line; scrollback is not a signal."""
    for line in reversed(pane_text.splitlines()):
        if line.strip():
            return line
    return ""


def pane_has_cursor_busy_chrome(pane_text: str) -> bool:
    """True when the latest line shows an in-flight run (not a safe wake target)."""
    text = last_nonempty_line(pane_text).lower()
    return any(marker in text for marker in _CURSOR_BUSY_MARKERS)


def pane_has_approval_modal(pane_text: str) -> bool:
    """Approval modals span multiple lines — scan a short trailing window."""
    lines = pane_text.strip().splitlines()[-15:]
    text = "\n".join(lines).lower()
    return any(marker in text for marker in _APPROVAL_MODAL_MARKERS)


def pane_at_prompt(pane_text: str) -> bool:
    """Idle prompt ready for bus injection.

    Busy chrome is decided only on the last non-empty line (so scrollback cannot
    permanently poison). Prompt glyphs use startswith on a short trailing
    window (last 3 non-empty lines): a live prompt sits at or adjacent to the
    bottom, and a wider window would treat wake-template '> WORKING DIR:' lines
    as at-prompt false positives (the expensive wrong direction).
    """
    if pane_has_cursor_busy_chrome(pane_text):
        return False
    lines = [line for line in pane_text.splitlines() if line.strip()][-3:]
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(glyph) for glyph in _PROMPT_GLYPHS):
            return True
    text = "\n".join(lines).lower()
    return any(marker in text for marker in _PROMPT_SUBSTRING_MARKERS)


def pane_looks_busy(pane_text: str) -> bool:
    return not pane_at_prompt(pane_text)


def _deferred_member_matches(member: str, agent: str, message: str) -> bool:
    try:
        payload = json.loads(member)
    except (json.JSONDecodeError, TypeError):
        return False
    return payload.get("agent") == agent and payload.get("message") == message


def enqueue_deferred_wake(
    r: redis.Redis,
    *,
    agent: str,
    message: str,
    now: float,
    attempts: int,
    enqueued_at: float | None,
) -> str:
    """Schedule a wake retry in Redis. Dedupes same (agent, message). Returns member JSON."""
    retained_enqueued_at = now if enqueued_at is None else enqueued_at
    retained_attempts = attempts
    for existing in r.zrange(DEFERRED_WAKE_ZSET, 0, -1):
        if not _deferred_member_matches(existing, agent, message):
            continue
        try:
            prior = json.loads(existing)
            retained_enqueued_at = min(
                retained_enqueued_at, float(prior.get("enqueued_at", retained_enqueued_at))
            )
            retained_attempts = max(retained_attempts, int(prior.get("attempts", 0)))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        r.zrem(DEFERRED_WAKE_ZSET, existing)
    member = json.dumps(
        {
            "agent": agent,
            "message": message,
            "attempts": retained_attempts,
            "enqueued_at": retained_enqueued_at,
        },
        sort_keys=True,
    )
    score = now + busy_retry_delay_seconds(retained_attempts)
    r.zadd(DEFERRED_WAKE_ZSET, {member: score})
    logger.info(
        "Deferred wake for %s attempts=%s due_in=%.1fs",
        agent,
        retained_attempts,
        busy_retry_delay_seconds(retained_attempts),
    )
    return member


def process_deferred_wakes(
    r: redis.Redis,
    *,
    now: float,
    wake_fn: Callable[..., Any],
) -> int:
    """Re-attempt due deferred wakes. Returns count successfully delivered."""
    due = r.zrangebyscore(DEFERRED_WAKE_ZSET, "-inf", now, start=0, num=50)
    delivered = 0
    for member in due:
        r.zrem(DEFERRED_WAKE_ZSET, member)
        try:
            payload = json.loads(member)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Dropping malformed deferred wake member: %s", exc)
            continue
        agent = payload.get("agent")
        message = payload.get("message")
        attempts = int(payload.get("attempts", 0))
        enqueued_at = float(payload.get("enqueued_at", now))
        if not isinstance(agent, str) or not isinstance(message, str):
            logger.warning("Dropping deferred wake with invalid agent/message")
            continue
        if now - enqueued_at > DEFERRED_WAKE_MAX_AGE_SECONDS:
            logger.warning(
                "Dropping deferred wake for %s — aged out after %.0fs",
                agent,
                now - enqueued_at,
            )
            continue
        if attempts >= DEFERRED_WAKE_MAX_ATTEMPTS:
            logger.warning(
                "Dropping deferred wake for %s — max attempts (%s)",
                agent,
                DEFERRED_WAKE_MAX_ATTEMPTS,
            )
            continue
        # Pass redis_client=None so a busy result does not double-enqueue inside wake_tmux.
        result = wake_fn(agent, message, redis_client=None)
        if result in (True, "sent"):
            delivered += 1
            continue
        # busy + blocked are transient; requeue. missing/error are terminal for this entry.
        if result in (False, "busy", "blocked"):
            enqueue_deferred_wake(
                r,
                agent=agent,
                message=message,
                now=now,
                attempts=attempts + 1,
                enqueued_at=enqueued_at,
            )
            continue
        logger.info(
            "Deferred wake for %s ended with status=%s (not requeued)",
            agent,
            result,
        )
    return delivered


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


def _defer_if_possible(
    redis_client: redis.Redis | None,
    agent: str,
    message: str,
) -> str:
    """Enqueue a busy wake when Redis is available; otherwise report busy only."""
    if redis_client is None:
        logger.info(
            "tmux busy for %s — no redis client on this call; caller must defer",
            agent,
        )
        return "busy"
    enqueue_deferred_wake(
        redis_client,
        agent=agent,
        message=message,
        now=time.time(),
        attempts=0,
        enqueued_at=None,
    )
    return "busy"


def _wake_tmux_sudo(
    agent: str,
    session: str,
    message: str,
    linux_user: str,
    redis_client: redis.Redis | None,
) -> str:
    """Wake a tmux agent running as a different Linux user via sudo."""
    sock_path = os.path.join(SHARED_TMUX_DIR, session)
    if not os.path.exists(sock_path):
        logger.warning(f"tmux socket not found for {agent} at {sock_path}")
        return "missing"

    try:
        # Check if at prompt
        check = subprocess.run(
            ["sudo", "-u", linux_user, "tmux", "-S", sock_path, "capture-pane", "-t", session, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        pane_text = check.stdout

        if pane_has_approval_modal(pane_text):
            logger.info(
                "tmux:%s (sudo:%s) blocked by approval modal — leaving inbox for %s",
                session,
                linux_user,
                agent,
            )
            return "blocked"

        if pane_looks_busy(pane_text):
            logger.info(
                "tmux:%s (sudo:%s) busy — deferring wake for %s",
                session,
                linux_user,
                agent,
            )
            return _defer_if_possible(redis_client, agent, message)

        first_line = message.split(chr(10))[0].replace("'", "")
        short_msg = first_line[:200] + "... [check inbox for full msg]" if len(message) > 200 else first_line[:300]
        cmd = f"[bus:{agent}] {short_msg}"

        subprocess.run(
            ["sudo", "-u", linux_user, "tmux", "-S", sock_path, "send-keys", "-t", session, "-l", cmd],
            timeout=5,
        )
        time.sleep(0.2)
        subprocess.run(
            ["sudo", "-u", linux_user, "tmux", "-S", sock_path, "send-keys", "-t", session, "Enter"],
            timeout=5,
        )
        logger.info(f"Woke tmux:{session} for {agent} via sudo -u {linux_user}")
        return "sent"
    except Exception as exc:
        logger.error(f"sudo tmux wake failed for {agent}: {exc}")
        return "error"


def wake_tmux(
    agent: str,
    message: str,
    redis_client: redis.Redis | None,
) -> str:
    """Send keys to an agent's tmux session (supports shared sockets + cross-user sudo).

    Returns status: sent | busy | blocked | missing | error.
    On busy with redis_client set, schedules a real Redis deferred retry (#594).
    """
    session = TMUX_SESSION_MAP.get(agent, agent)

    # Cross-user agents need sudo
    linux_user = CROSS_USER_AGENTS.get(agent)
    if linux_user:
        return _wake_tmux_sudo(agent, session, message, linux_user, redis_client)

    sessions = get_tmux_sessions()
    if session not in sessions:
        logger.warning(f"tmux session '{session}' not found for {agent}")
        return "missing"

    # Find socket (shared or default)
    sock_args = _get_tmux_socket(session)

    try:
        # Check if Claude Code / Gemini CLI is waiting for input (not mid-response)
        # by checking if the pane is at a prompt
        check = subprocess.run(
            ["tmux"] + sock_args + ["capture-pane", "-t", session, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        # Classify from raw pane text: busy/prompt use last non-empty line;
        # approval modals scan a short trailing multi-line window.
        pane_text = check.stdout

        # Codex approval modals are interactive, but they are not safe wake targets:
        # a bus-injected Enter would approve or cancel unrelated work.
        if pane_has_approval_modal(pane_text):
            logger.info(
                "tmux:%s blocked by approval modal — leaving message in inbox for %s",
                session,
                agent,
            )
            return "blocked"

        if pane_looks_busy(pane_text):
            logger.info(
                "tmux:%s busy (not at prompt / mid-run chrome) — deferring wake for %s",
                session,
                agent,
            )
            return _defer_if_possible(redis_client, agent, message)

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
        # 0.5s gives Claude Code time to finish rendering before Enter fires
        time.sleep(0.5)
        submit_key = "C-m" if agent in ("river", "gemini") else "Enter"
        subprocess.run(
            ["tmux"] + sock_args + ["send-keys", "-t", session, submit_key],
            timeout=5,
        )
        logger.info(f"Woke tmux:{session} for {agent} (sent to prompt)")
        return "sent"
    except Exception as e:
        logger.error(f"tmux wake failed for {agent}: {e}")
        return "error"


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
    if agent == "broadcast" or agent.endswith("-canary"):
        logger.debug("Wake channel %s is synthetic; wake skipped", agent)
        return

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
        wake_tmux(agent, message, redis_client=r)
    elif routing == "openclaw":
        wake_openclaw(agent, message, r)
    elif routing == "both":
        wake_tmux(agent, message, redis_client=r)
        wake_openclaw(agent, message, r)
    elif routing in ("none", "mcp", "daemon"):
        logger.debug("Routing '%s' for %s is inbox-only; wake skipped", routing, agent)
    else:
        logger.warning(f"Unknown routing '{routing}' for {agent}")


def format_tmux_wake_message(agent: str, source: str, text: str) -> str:
    """Render an injected tmux message that makes reply semantics explicit."""
    source = source or "unknown"
    text = text or ""

    if source.startswith("agent:"):
        return f"[{source}] {text}"

    return f"[{source}] {text}"


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

    running = True

    def shutdown(sig, frame):
        nonlocal running
        running = False
        logger.info(f"Received signal {sig}, shutting down...")
        pubsub.close()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info(
        "Listening for wake signals (deferred busy-retry poll every %.1fs)...",
        DEFERRED_POLL_TIMEOUT_SECONDS,
    )

    try:
        # get_message + timeout (not listen()) so deferred busy retries run while idle.
        while running:
            try:
                process_deferred_wakes(r, now=time.time(), wake_fn=wake_tmux)
            except redis.RedisError as exc:
                logger.warning("Deferred wake poll failed (continuing): %s", exc)
            msg = pubsub.get_message(timeout=DEFERRED_POLL_TIMEOUT_SECONDS)
            if msg is None:
                continue
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
                if source == "unknown":
                    sender = payload.get("from")
                    if isinstance(sender, str) and sender:
                        source = f"agent:{sender}"
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
                if isinstance(text, str):
                    try:
                        inner_text = json.loads(text)
                        if isinstance(inner_text, dict):
                            source = inner_text.get("source", source)
                            text = inner_text.get("text", text)
                    except (json.JSONDecodeError, TypeError):
                        pass
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

            if agent == "broadcast" or agent.endswith("-canary"):
                logger.debug("Wake channel %s is synthetic; wake skipped", agent)
                continue

            logger.info(f"Wake signal: {agent} from {source}: {text[:80]}")
            handle_wake(agent, format_tmux_wake_message(agent, source, text), r)
    except (OSError, ValueError, redis.RedisError) as exc:
        if running:
            raise
        logger.debug("Wake daemon listener closed during shutdown: %s", exc)


if __name__ == "__main__":
    main()
