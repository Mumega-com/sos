#!/usr/bin/env python3
"""Bus round-trip canary.

Every 60s, one canary identity sends `[probe] {ts}` to a peer canary identity
through the bus REST bridge, the peer reads its inbox and replies, and the
origin identity reads the reply. Round-trip > budget or any failure pages.

Mechanical check:
  Kill bus-bridge for 60s, verify probe failure detected within next probe
  cycle. Restart bus-bridge, verify probe recovers within next cycle.

Catches:
  - bus-bridge POST broken (probe send fails)
  - mcp__sos__inbox stale-archive (probe never lands in inbox)
  - agent dormancy during active sprint (probe sent but no reply)

Implementation notes:
  - Speaks REST to the bus bridge on :6380. The bridge is the underlying
    transport for MCP send/inbox flows in the default SOS deployment; probing
    the REST surface exercises the same bus-stream invariant.
  - Single process simulates both ends. Two distinct tokens are used so each
    side authenticates as its own identity, mirroring real-agent posture.
  - Synchronous urllib (stdlib): once-per-minute cadence has no need
    for asyncio; sequential probe is easier to reason about under failure.
  - Page failures don't crash the daemon. The bus-down state is exactly
    what we're alerting on; trying to alert via the down bus is best-effort.
    Discord webhook + journalctl are independent fall-backs.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

BUS_BRIDGE_URL = os.environ.get("BUS_BRIDGE_URL", "http://localhost:6380")
LOOM_CANARY_TOKEN = os.environ.get("LOOM_CANARY_TOKEN", "")
KASRA_CANARY_TOKEN = os.environ.get("KASRA_CANARY_TOKEN", "")
ORIGIN_CANARY_AGENT = os.environ.get("ORIGIN_CANARY_AGENT", "loom-canary")
PEER_CANARY_AGENT = os.environ.get("PEER_CANARY_AGENT", "kasra-canary")
ALERT_AGENT = os.environ.get("BUS_CANARY_ALERT_AGENT", "loom")
PROBE_INTERVAL_SEC = int(os.environ.get("F11_PROBE_INTERVAL_SEC", "60"))
ROUND_TRIP_BUDGET_SEC = float(os.environ.get("F11_ROUND_TRIP_BUDGET_SEC", "5"))
INBOX_POLL_INTERVAL_SEC = 0.2
HTTP_TIMEOUT_SEC = 3.0

# Discord paging.
# `channels.<name>` values are channel snowflakes; `agents.<name>` /
# `system.<name>` values are full webhook URLs. We resolve in that order.
# Channel-snowflake path uses Discord Bot API with DISCORD_BOT_TOKEN; webhook
# path posts directly. Either silent-fails so paging never crashes the daemon.
DISCORD_PAGE_CHANNEL = os.environ.get("F11_PAGE_CHANNEL", "alerts")
DISCORD_WEBHOOKS_FILE = os.environ.get(
    "DISCORD_WEBHOOKS_FILE", os.path.expanduser("~/.sos/discord_webhooks.json")
)
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

# Re-page suppression: alert state-change only (don't spam every 60s).
_last_alert_state: str | None = None  # "ok" | "fail" | None at startup
_consecutive_failures = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _http(
    method: str,
    path: str,
    token: str,
    body: dict | None = None,
    timeout: float = HTTP_TIMEOUT_SEC,
) -> dict:
    url = BUS_BRIDGE_URL.rstrip("/") + path
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def _send(token: str, from_agent: str, to_agent: str, text: str) -> None:
    _http("POST", "/send", token, {"from": from_agent, "to": to_agent, "text": text})


def _inbox(token: str, agent: str, limit: int = 5) -> list[dict]:
    qs = urllib.parse.urlencode({"agent": agent, "limit": str(limit)})
    res = _http("GET", "/inbox?" + qs, token)
    return res.get("messages", []) or []


def _wait_for_text(token: str, agent: str, needle: str, deadline: float) -> bool:
    """Poll inbox until needle appears in any message text, or deadline passes."""
    while time.monotonic() < deadline:
        try:
            for msg in _inbox(token, agent, limit=10):
                if needle in msg.get("text", ""):
                    return True
        except Exception:
            # treat transient inbox failure as not-yet-found; outer loop budgets time
            pass
        time.sleep(INBOX_POLL_INTERVAL_SEC)
    return False


def probe() -> dict:
    """One full round-trip. Raises on any failure path. Returns {latency_sec, probe_id}."""
    if not LOOM_CANARY_TOKEN or not KASRA_CANARY_TOKEN:
        raise RuntimeError("LOOM_CANARY_TOKEN / KASRA_CANARY_TOKEN env vars required")

    probe_id = uuid.uuid4().hex[:12]
    ts = _now_iso()
    forward_text = f"[probe:{probe_id}] {ts}"
    reply_text = f"[probe:{probe_id}:reply] {ts}"

    t0 = time.monotonic()
    deadline = t0 + ROUND_TRIP_BUDGET_SEC

    # 1. origin canary -> peer canary
    _send(LOOM_CANARY_TOKEN, ORIGIN_CANARY_AGENT, PEER_CANARY_AGENT, forward_text)

    # 2. peer canary observes the probe
    if not _wait_for_text(
        KASRA_CANARY_TOKEN,
        PEER_CANARY_AGENT,
        f"probe:{probe_id}]",
        deadline,
    ):
        raise RuntimeError(
            f"probe {probe_id} forward leg: {PEER_CANARY_AGENT} inbox missing probe within budget"
        )

    # 3. peer canary -> origin canary reply
    _send(KASRA_CANARY_TOKEN, PEER_CANARY_AGENT, ORIGIN_CANARY_AGENT, reply_text)

    # 4. origin canary observes the reply
    if not _wait_for_text(
        LOOM_CANARY_TOKEN,
        ORIGIN_CANARY_AGENT,
        f"probe:{probe_id}:reply",
        deadline,
    ):
        raise RuntimeError(
            f"probe {probe_id} return leg: {ORIGIN_CANARY_AGENT} inbox missing reply within budget"
        )

    return {"probe_id": probe_id, "latency_sec": time.monotonic() - t0}


def _discord_target(channel: str) -> tuple[str, str] | None:
    """Return ('webhook', url) or ('channel', snowflake_id) for the given channel name."""
    try:
        with open(DISCORD_WEBHOOKS_FILE) as f:
            data = json.load(f)
    except Exception:
        return None
    val = data.get("channels", {}).get(channel)
    if val and isinstance(val, str):
        if val.startswith("http"):
            return ("webhook", val)
        return ("channel", val)
    val = data.get("agents", {}).get(channel) or data.get("system", {}).get(channel)
    if val and isinstance(val, str) and val.startswith("http"):
        return ("webhook", val)
    return None


def _page_discord(text: str) -> None:
    try:
        target = _discord_target(DISCORD_PAGE_CHANNEL)
        if not target:
            return
        kind, val = target
        body = json.dumps({"content": text[:1900]}).encode()
        if kind == "webhook":
            req = urllib.request.Request(
                val,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        elif kind == "channel" and DISCORD_BOT_TOKEN:
            req = urllib.request.Request(
                f"https://discord.com/api/v10/channels/{val}/messages",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                },
                method="POST",
            )
        else:
            return
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def _page_bus(text: str) -> None:
    """Best-effort bus alert. Silently skip if bus is down, which is
    often exactly the failure being reported."""
    if not LOOM_CANARY_TOKEN:
        return
    try:
        _send(LOOM_CANARY_TOKEN, "bus-canary", ALERT_AGENT, text)
    except Exception:
        pass


def page(reason: str) -> None:
    text = f"[F-11 ALERT] bus round-trip canary failed: {reason}"
    print(f"[bus-canary] PAGE {text}", file=sys.stderr, flush=True)
    try:
        _page_discord(text)
    except Exception as exc:  # paging must never crash the daemon
        print(f"[bus-canary] page-discord error: {exc}", file=sys.stderr, flush=True)
    try:
        _page_bus(text)
    except Exception as exc:
        print(f"[bus-canary] page-bus error: {exc}", file=sys.stderr, flush=True)


def page_recovery(latency_sec: float) -> None:
    text = f"[F-11 RECOVERY] bus round-trip canary recovered (latency={latency_sec:.3f}s)"
    print(f"[bus-canary] {text}", flush=True)
    try:
        _page_discord(text)
    except Exception as exc:
        print(f"[bus-canary] recovery page-discord error: {exc}", file=sys.stderr, flush=True)
    try:
        _page_bus(text)
    except Exception as exc:
        print(f"[bus-canary] recovery page-bus error: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    global _last_alert_state, _consecutive_failures
    print(
        f"[bus-canary] starting - interval={PROBE_INTERVAL_SEC}s "
        f"budget={ROUND_TRIP_BUDGET_SEC}s url={BUS_BRIDGE_URL}",
        flush=True,
    )
    while True:
        cycle_start = time.monotonic()
        try:
            result = probe()
            latency = result["latency_sec"]
            if latency > ROUND_TRIP_BUDGET_SEC:
                _consecutive_failures += 1
                if _last_alert_state != "fail":
                    page(
                        f"latency {latency:.3f}s exceeds {ROUND_TRIP_BUDGET_SEC}s "
                        f"budget (probe {result['probe_id']})"
                    )
                    _last_alert_state = "fail"
            else:
                if _last_alert_state == "fail":
                    page_recovery(latency)
                print(
                    f"[bus-canary] OK probe={result['probe_id']} latency={latency:.3f}s",
                    flush=True,
                )
                _consecutive_failures = 0
                _last_alert_state = "ok"
        except Exception as exc:
            _consecutive_failures += 1
            if _last_alert_state != "fail":
                page(f"{type(exc).__name__}: {exc}")
                _last_alert_state = "fail"
            else:
                # Still failing; log without re-paging.
                print(
                    f"[bus-canary] FAIL ({_consecutive_failures}x) {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        elapsed = time.monotonic() - cycle_start
        time.sleep(max(0, PROBE_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
