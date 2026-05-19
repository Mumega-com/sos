#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_DIR="${OPENCLAW_DIR:-/home/sos/vendor/openclaw}"
PORT="${OPENCLAW_PORT:-18789}"

if [ ! -d "$OPENCLAW_DIR" ]; then
  echo "OpenClaw not found at $OPENCLAW_DIR"
  exit 1
fi

cd "$OPENCLAW_DIR"

if [ ! -d node_modules ]; then
  echo "Missing node_modules; run: pnpm install"
  exit 1
fi

if [ ! -d dist ]; then
  echo "Missing dist; run: pnpm build"
  exit 1
fi

echo "Starting OpenClaw gateway on loopback:${PORT}..."
node dist/index.js gateway --bind loopback --port "$PORT" --force > /tmp/openclaw-gateway.log 2>&1 &
PID=$!

sleep 2
if ss -ltnp 2>/dev/null | grep -q ":${PORT}"; then
  echo "Gateway running (pid $PID)."
  echo "Logs: /tmp/openclaw-gateway.log"
else
  echo "Gateway did not start; see /tmp/openclaw-gateway.log"
  kill "$PID" >/dev/null 2>&1 || true
  exit 1
fi

echo "Stopping gateway..."
kill "$PID" >/dev/null 2>&1 || true
echo "Done."
