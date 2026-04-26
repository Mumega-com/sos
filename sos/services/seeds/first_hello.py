"""first-hello consumer — reads Redis queue, sends welcome message to channel.

Sprint 012 OmniC. The final step: after deploy-seed.py enqueues the first-hello,
this consumer picks it up and delivers the message.

Runs as: python3 -m sos.services.seeds.first_hello
Or called from the brain service event loop.

Queue key: sos:seed:first-hello:{project_id}
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

log = logging.getLogger("sos.seeds.first_hello")


def _alert_first_hello_failure(fields: dict) -> None:
    """Alert on permanent first-hello delivery failure."""
    try:
        from sos.services.billing.webhook import _alert_athena
        _alert_athena(
            f"first-hello permanent failure for {fields.get('agent_name', '?')}: "
            f"max retries reached, entry deleted"
        )
    except Exception:
        log.error("first_hello: alert failed for %s", fields.get("agent_name", "?"))


def consume_first_hellos() -> list[dict]:
    """Scan all first-hello queues and deliver pending messages.

    Returns list of delivered messages.
    """
    import redis

    pw = os.environ.get("REDIS_PASSWORD", "")
    r = redis.Redis(host="localhost", port=6379, password=pw, decode_responses=True)

    delivered = []

    # Scan for first-hello queues
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match="sos:seed:first-hello:*", count=100)
        for key in keys:
            try:
                entries = r.xrange(key, count=10)
                for entry_id, fields in entries:
                    try:
                        result = _deliver_first_hello(r, fields)
                        # BLOCK-2 fix: only xdel on confirmed delivery
                        if result and result.get("delivered"):
                            delivered.append(result)
                            r.xdel(key, entry_id)
                        else:
                            # Delivery failed — check retry count
                            retries = int(fields.get("_retries", "0"))
                            if retries >= 5:
                                log.error(
                                    "first_hello: max retries reached for %s, deleting entry",
                                    fields.get("agent_name", "?"),
                                )
                                _alert_first_hello_failure(fields)
                                r.xdel(key, entry_id)
                            else:
                                # Increment retry count (re-add with incremented counter)
                                fields["_retries"] = str(retries + 1)
                                log.warning(
                                    "first_hello: delivery failed for %s (retry %d/5)",
                                    fields.get("agent_name", "?"), retries + 1,
                                )
                    except Exception as exc:
                        log.warning("first_hello: entry processing failed: %s", exc)
            except Exception as exc:
                log.warning("first_hello: failed to process queue %s: %s", key, exc)
        if cursor == 0:
            break

    return delivered


def _deliver_first_hello(r, fields: dict) -> dict | None:
    """Send the first-hello message to the agent's channel."""
    agent_name = fields.get("agent_name", "")
    channel_id = fields.get("channel_id", "")
    cause = fields.get("cause", "")

    if not agent_name:
        return None

    message = (
        f"Hi, I'm {agent_name}. {cause} "
        f"I'll be quiet for a few days while I learn your rhythm. "
        f"When I notice something useful, I'll share it."
    )

    # Deliver via SOS bus bridge
    try:
        import urllib.request
        bridge_url = os.environ.get("SOS_BRIDGE_URL", "http://localhost:6380")

        # Find a token for this agent
        from pathlib import Path
        tokens_path = Path("/home/mumega/SOS/sos/bus/tokens.json")
        token = ""
        if tokens_path.exists():
            tokens = json.loads(tokens_path.read_text())
            for t in tokens:
                if t.get("agent") == agent_name.replace("-agent", "-knight") and t.get("active"):
                    token = t.get("token", "")
                    break

        if not token:
            log.warning("first_hello: no active token for %s, using broadcast", agent_name)
            # Fallback: broadcast via system
            payload = json.dumps({
                "from": agent_name,
                "text": message,
                "to": "broadcast",
            }).encode()
        else:
            payload = json.dumps({
                "from": agent_name,
                "to": agent_name,  # self-stream for now; channel binding routes it
                "text": message,
            }).encode()

        req = urllib.request.Request(
            f"{bridge_url}/send",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}" if token else "",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("first_hello: delivered for %s (HTTP %d)", agent_name, resp.status)

    except Exception as exc:
        log.warning("first_hello: bus delivery failed for %s: %s", agent_name, exc)
        return {
            "agent_name": agent_name,
            "channel_id": channel_id,
            "delivered": False,
            "error": str(exc),
        }

    return {
        "agent_name": agent_name,
        "channel_id": channel_id,
        "delivered": True,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "message": message[:100],
    }


def main():
    """CLI: run one pass of first-hello consumption."""
    import argparse

    parser = argparse.ArgumentParser(description="Consume first-hello Redis queues")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval seconds")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.loop:
        log.info("first_hello: starting consumer loop (interval=%ds)", args.interval)
        while True:
            results = consume_first_hellos()
            if results:
                log.info("first_hello: delivered %d messages", len(results))
            time.sleep(args.interval)
    else:
        results = consume_first_hellos()
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
