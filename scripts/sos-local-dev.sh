#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="$ROOT/.sos/local"
ENV_FILE="$LOCAL_DIR/dev.env"
PYTHON="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage: scripts/sos-local-dev.sh <init|up|doctor|status|down>

Commands:
  init     Generate local dev tokens and .sos/local/dev.env
  up       Start Redis, bus bridge, MCP, and Squad locally
  doctor   Run the public smoke doctor
  status   Show local service health
  down     Stop services started by this script
EOF
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

init_profile() {
  cd "$ROOT"
  "$PYTHON" -c "from sos.cli.local import init_profile; init_profile()"
}

run_migrations() {
  cd "$ROOT"
  "$PYTHON" -c "from sos.cli.local import run_migrations; raise SystemExit(run_migrations())"
}

start_redis() {
  mkdir -p "$LOCAL_DIR/redis"
  local redis_port="${REDIS_PORT:-16379}"
  if redis-cli -p "$redis_port" ping >/dev/null 2>&1; then
    echo "Redis already running on :$redis_port"
    return
  fi
  if ! command -v redis-server >/dev/null 2>&1; then
    echo "redis-server not found. Install Redis or start it separately on :$redis_port." >&2
    exit 1
  fi
  redis-server --daemonize yes --dir "$LOCAL_DIR/redis" --pidfile "$LOCAL_DIR/redis.pid" --port "$redis_port" --save "" --appendonly no
  echo "Started Redis on :$redis_port"
}

start_service() {
  local name="$1"
  shift
  local pid_file="$LOCAL_DIR/$name.pid"
  local log_file="$LOCAL_DIR/$name.log"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    echo "$name already running (pid $(cat "$pid_file"))"
    return
  fi
  cd "$ROOT"
  setsid "$@" >"$log_file" 2>&1 < /dev/null &
  echo "$!" > "$pid_file"
  echo "Started $name (pid $!, log $log_file)"
  sleep 0.5
  if ! kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    echo "$name exited during startup. Last log lines:" >&2
    tail -40 "$log_file" >&2 || true
    exit 1
  fi
}

wait_http() {
  local name="$1"
  local url="$2"
  for _ in $(seq 1 40); do
    if "$PYTHON" - "$url" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=1.0) as response:
        raise SystemExit(0 if response.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
    then
      echo "$name ready ($url)"
      return
    fi
    sleep 0.25
  done
  echo "$name did not become ready at $url" >&2
  exit 1
}

cmd="${1:-}"
case "$cmd" in
  init)
    init_profile
    echo "Wrote $ENV_FILE"
    ;;
  up)
    init_profile
    load_env
    run_migrations
    start_redis
    start_service bus "$PYTHON" -m sos.bus.bridge
    start_service mcp "$PYTHON" -m sos.mcp.sos_mcp_sse
    start_service squad "$PYTHON" -m sos.services.squad.app
    wait_http bus "$SOS_BUS_URL/health"
    wait_http mcp "$SOS_MCP_HEALTH_URL/health"
    wait_http squad "$SOS_SQUAD_URL/health"
    echo "Local SOS profile started. Run: scripts/sos-local-dev.sh doctor"
    ;;
  doctor)
    init_profile
    load_env
    cd "$ROOT"
    "$PYTHON" -m sos.cli local doctor
    ;;
  status)
    for name in redis bus mcp squad; do
      pid_file="$LOCAL_DIR/$name.pid"
      if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
        echo "$name: running (pid $(cat "$pid_file"))"
      else
        echo "$name: not running"
      fi
    done
    ;;
  down)
    for name in bus mcp squad redis; do
      pid_file="$LOCAL_DIR/$name.pid"
      if [[ -f "$pid_file" ]]; then
        kill "$(cat "$pid_file")" >/dev/null 2>&1 || true
        rm -f "$pid_file"
        echo "Stopped $name"
      fi
    done
    ;;
  *)
    usage
    exit 1
    ;;
esac
