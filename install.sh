#!/bin/sh
# SOS — Sovereign Operating System installer
# curl -sf https://raw.githubusercontent.com/Mumega-com/sos/main/install.sh | sh
set -e

REPO="https://github.com/Mumega-com/sos.git"
log()  { printf "\033[1;34m==>\033[0m %s\n" "$1"; }
ok()   { printf "\033[1;32m OK\033[0m %s\n" "$1"; }
warn() { printf "\033[1;33m !!\033[0m %s\n" "$1"; }
log "Checking prerequisites..."
MISSING="" ; PY=""
for cmd in python3.13 python3.12 python3.11 python3; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
      PY="$cmd"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  MISSING="$MISSING python3.11+"
else
  ok "Python: $PY ($ver)"
fi

# Redis
if command -v redis-server >/dev/null 2>&1; then
  ok "redis-server found"
else
  MISSING="$MISSING redis-server"
fi

# Git
if command -v git >/dev/null 2>&1; then
  ok "git found"
else
  MISSING="$MISSING git"
fi

if [ -n "$MISSING" ]; then
  warn "Missing:$MISSING"
  echo "  Ubuntu/Debian: sudo apt install -y python3.11 python3.11-venv redis-server git"
  echo "  macOS:         brew install python@3.11 redis git"
  exit 1
fi

if [ -f "sos/__init__.py" ]; then
  ok "Already in SOS directory"
  SOS_DIR="$(pwd)"
elif [ -f "SOS/sos/__init__.py" ]; then
  ok "Found SOS/ subdirectory"
  SOS_DIR="$(pwd)/SOS"
else
  log "Cloning SOS..."
  git clone "$REPO" SOS
  SOS_DIR="$(pwd)/SOS"
fi
cd "$SOS_DIR"

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    ok "Created .env from .env.example"
  else
    warn "No .env.example found — skipping .env creation"
  fi
else
  ok ".env already exists"
fi

log "Installing Python dependencies..."
if [ -f requirements.txt ]; then
  "$PY" -m pip install -r requirements.txt --quiet 2>/dev/null || \
  "$PY" -m pip install -r requirements.txt --quiet --user
  ok "Dependencies installed"
else
  warn "No requirements.txt found"
fi

if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
  ok "Redis is running"
else
  log "Starting Redis..."
  if [ "$(uname)" = "Darwin" ]; then
    brew services start redis 2>/dev/null || redis-server --daemonize yes
  else
    sudo systemctl start redis-server 2>/dev/null || redis-server --daemonize yes
  fi
  if redis-cli ping >/dev/null 2>&1; then
    ok "Redis started"
  else
    warn "Could not start Redis — start it manually"
  fi
fi

echo ""
printf "\033[1;32mSOS installed.\033[0m Run '\033[1m$PY -m sos.cli.init\033[0m' to set up.\n"
