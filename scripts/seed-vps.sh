#!/usr/bin/env bash
# seed-vps.sh — stand up a SOVEREIGN Mumega/SOS seed on a fresh Ubuntu VPS.
#
# This is the "one command" install of a tenant seed. Verified end-to-end on the
# viamar VPS (Ubuntu 24.04, 2 vCPU / 4 GB) on 2026-05-30. Idempotent — safe to re-run.
#
#   curl -fsSL https://raw.githubusercontent.com/Mumega-com/sos/main/scripts/seed-vps.sh | bash
#   # or: git clone … && bash scripts/seed-vps.sh
#
# What it produces: the tenant's OWN sovereign stack on THEIR box — Redis + the SOS
# engine + the autonomy (River) loop with FRC physics, with the tenant's own tokens.
# NOT pointed at anyone else's bus. The membrane (a cross-in token for a helper agent)
# and the memory backend are configured separately (see MEMORY + MEMBRANE below).
set -euo pipefail

SOS_DIR="${SOS_DIR:-$HOME/SOS}"
SOS_REPO="${SOS_REPO:-https://github.com/Mumega-com/sos.git}"

say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

say "1/7 system deps (redis, python venv, git)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq redis-server git python3-venv python3-pip
sudo systemctl enable --now redis-server
[ "$(redis-cli ping 2>/dev/null)" = "PONG" ] && echo "redis: PONG" || { echo "redis not responding"; exit 1; }

say "2/7 clone the public SOS kernel"
[ -d "$SOS_DIR/.git" ] || git clone --depth 1 "$SOS_REPO" "$SOS_DIR"
cd "$SOS_DIR"

say "3/7 venv + install (light deps; no Postgres)"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -e . -q
python -c "import sos" && echo "sos import OK"

say "4/7 sovereign identity + squad DB"
sos local init        # -> .sos/local/dev.env + tokens.json  (THIS box's own tokens)
sos local migrate     # squad SQLite migrations

say "5/7 config (.env)"
[ -f .env ] || cp .env.example .env
# --- MEMORY backend (choose one) -------------------------------------------------
#   A) Cloudflare (recommended for a seed — light, sovereign, no Postgres):
#        echo 'SOS_MEMORY_BACKEND=cloudflare'  >> .env
#        echo 'CF_D1_DATABASE_ID=<tenant-d1>'   >> .env
#        echo 'CF_VECTORIZE_INDEX=<tenant-idx>' >> .env
#        echo 'CLOUDFLARE_API_TOKEN=<scoped>'   >> .env
#   B) Postgres+pgvector (heavier): install postgres + pgvector + the mirror pkg, set DATABASE_URL.
# --- MODEL body ------------------------------------------------------------------
#   gcloud ADC on the tenant's OWN GCP project (run once):  gcloud auth application-default login
#   or set a key:  echo 'GEMINI_API_KEY=<tenant-key>' >> .env

say "6/7 start services (daemonized)"
mkdir -p "$SOS_DIR/.sos"
for svc in engine autonomy; do            # add 'memory' once the backend above is set
  pgrep -f "sos start $svc" >/dev/null || { nohup sos start "$svc" >> "$SOS_DIR/.sos/start.log" 2>&1 & sleep 4; }
done

say "7/7 verify"
sos doctor || true
echo
echo "Seed up. Next: (MEMBRANE) mint a cross-in token for a helper agent + create the"
echo "tenant's squad; (PERSIST) install systemd units so it survives reboot; (AGENT)"
echo "point the operator agent (Claude Code) at THIS local stack, not a foreign bus."
