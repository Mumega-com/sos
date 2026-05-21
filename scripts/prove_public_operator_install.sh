#!/usr/bin/env bash
set -euo pipefail

PROOF_ROOT="${PROOF_ROOT:-/tmp/sos-public-operator-proof}"
REPO_URL="${SOS_PUBLIC_REPO_URL:-https://github.com/Mumega-com/sos.git}"
REF="${SOS_PUBLIC_REF:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MCP_PORT="${SOS_PROOF_MCP_PORT:-6793}"

REDIS_PID=""
MCP_PID=""

cleanup() {
  if [[ -n "${MCP_PID}" ]] && kill -0 "${MCP_PID}" 2>/dev/null; then
    kill "${MCP_PID}" 2>/dev/null || true
    wait "${MCP_PID}" 2>/dev/null || true
  fi
  if [[ -n "${REDIS_PID}" ]] && kill -0 "${REDIS_PID}" 2>/dev/null; then
    kill "${REDIS_PID}" 2>/dev/null || true
    wait "${REDIS_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

log() {
  printf '[sos-proof] %s\n' "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'missing required command: %s\n' "$1" >&2
    exit 2
  fi
}

require_cmd git
require_cmd "$PYTHON_BIN"

rm -rf "$PROOF_ROOT"
mkdir -p "$PROOF_ROOT"

log "cloning ${REPO_URL} into ${PROOF_ROOT}/repo"
git clone "$REPO_URL" "$PROOF_ROOT/repo" >/dev/null
git -C "$PROOF_ROOT/repo" checkout "$REF" >/dev/null
cd "$PROOF_ROOT/repo"

log "creating venv"
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
log "installing package with documented editable install"
python -m pip install -e . >/dev/null

log "checking CLI"
sos version
sos doctor || true

if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
  log "using existing Redis on localhost:6379"
else
  require_cmd redis-server
  log "starting temporary Redis on localhost:6379"
  mkdir -p "$PROOF_ROOT/redis"
  redis-server --port 6379 --save "" --appendonly no --dir "$PROOF_ROOT/redis" >"$PROOF_ROOT/redis.log" 2>&1 &
  REDIS_PID="$!"
  for _ in $(seq 1 50); do
    if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
  redis-cli ping >/dev/null
fi

log "starting MCP gateway on port ${MCP_PORT}"
SOS_MCP_PORT="$MCP_PORT" python -m sos.mcp.sos_mcp_sse >"$PROOF_ROOT/mcp.log" 2>&1 &
MCP_PID="$!"
for _ in $(seq 1 50); do
  if python - <<PY >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:${MCP_PORT}/health", timeout=1).read()
PY
  then
    break
  fi
  sleep 0.2
done
python - <<PY
import json
import urllib.request
body = urllib.request.urlopen("http://127.0.0.1:${MCP_PORT}/health", timeout=2).read()
print("[sos-proof] mcp health", json.loads(body.decode()).get("status"))
PY

log "running public tool proof"
python - <<'PY'
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from sos.mcp import sos_mcp_sse as sse


def text(result: dict) -> str:
    return result["content"][0]["text"]


class EmptyRegistry:
    async def hgetall(self, key: str) -> dict[str, str]:
        return {}


async def main() -> None:
    proof_id = uuid4().hex[:8]
    project = f"proof-{proof_id}"
    agent = f"proof-agent-{proof_id}"
    r = sse._get_redis()

    empty = await sse._get_agent_statuses(EmptyRegistry())
    assert empty == [], empty

    auth = sse.MCPAuthContext(
        token="proof-token",
        tenant_id=project,
        is_system=False,
        source="public-proof",
        agent_name=agent,
        scope="agent",
        permissions=["send", "inbox", "broadcast", "recall", "status", "peers"],
    )

    await r.hset(
        "sos:registry:agents",
        agent,
        json.dumps({"type": "local", "model": "proof", "role": "proof"}),
    )
    await r.hset(
        f"sos:registry:{agent}",
        mapping={"project": project, "session_id": f"proof-{proof_id}", "model": "proof"},
    )

    sent = text(await sse.handle_tool("send", {"to": agent, "text": "proof hello"}, auth))
    assert "Sent to" in sent, sent

    inbox = text(await sse.handle_tool("inbox", {"agent": agent, "limit": 5}, auth))
    assert "proof hello" in inbox, inbox

    broadcast = text(await sse.handle_tool("broadcast", {"text": "proof broadcast"}, auth))
    assert "Broadcast to" in broadcast, broadcast

    peers = text(await sse.handle_tool("peers", {}, auth))
    assert agent in peers, peers

    status = text(await sse.handle_tool("status", {}, auth))
    assert agent in status, status

    recall = text(await sse.handle_tool("recall", {"query": "proof", "limit": 1}, auth))
    assert recall, "recall returned empty response"

    await r.hdel("sos:registry:agents", agent)
    await r.delete(f"sos:registry:{agent}", f"sos:stream:project:{project}:channel:private:agent:{agent}")
    await r.aclose()

    print("[sos-proof] empty registry: ok")
    print("[sos-proof] send/inbox: ok")
    print("[sos-proof] broadcast: ok")
    print("[sos-proof] peers: ok")
    print("[sos-proof] status: ok")
    print(f"[sos-proof] recall: {recall[:80]}")


asyncio.run(main())
PY

log "proof complete"
