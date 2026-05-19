#!/usr/bin/env bash
# sos pair-agent — client for the SaaS service's /sos/pairing protocol.
#
# Lives alongside the service it speaks to (sos/cli/ — user-facing
# pairing flow). Ops provisioning lives in scripts/tenant-setup.sh.
#
# Usage:
#   sos/cli/pair-agent.sh <agent-name> --skills a,b,c [--role agent] \
#       [--model claude:opus-4-7] [--host http://127.0.0.1:8000]
#
# What it does:
#   1. Generates an ed25519 keypair under ~/.sos/keys/<agent>/
#   2. Requests a nonce from GET  /sos/pairing/nonce?agent_name=<agent>
#   3. Signs the nonce raw bytes with the private key
#   4. Submits POST /sos/pairing  with pubkey + signature + skills + role + provider
#   5. Writes the returned bearer token to ~/.sos/token (0600)
#   6. Smoke-tests host liveness via GET /health
#
# Exit codes:
#   0  success — token is live
#   1  bad arguments
#   2  HTTP / pairing error
#   3  missing tooling (openssl, curl, jq)

set -euo pipefail

# ── dependencies ─────────────────────────────────────────────────────────────
for bin in openssl curl jq; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "error: required tool '$bin' not found in PATH" >&2
    exit 3
  fi
done

# ── arg parsing ──────────────────────────────────────────────────────────────
AGENT=""
SKILLS=""
ROLE="agent"
MODEL="claude:sonnet-4-6"
HOST="${SOS_SAAS_HOST:-http://127.0.0.1:8000}"

usage() {
  sed -n '2,21p' "$0" >&2
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

AGENT="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skills)    SKILLS="$2"; shift 2 ;;
    --role)      ROLE="$2";   shift 2 ;;
    --model)     MODEL="$2";  shift 2 ;;
    --host)      HOST="$2";   shift 2 ;;
    -h|--help)   usage ;;
    *) echo "error: unknown flag: $1" >&2; usage ;;
  esac
done

if [[ -z "$AGENT" || -z "$SKILLS" ]]; then
  echo "error: <agent-name> and --skills are required" >&2
  usage
fi

if ! [[ "$AGENT" =~ ^[a-z][a-z0-9-]{1,63}$ ]]; then
  echo "error: agent name must be lowercase [a-z0-9-], 2-64 chars" >&2
  exit 1
fi

# ── keypair ──────────────────────────────────────────────────────────────────
KEY_DIR="$HOME/.sos/keys/$AGENT"
mkdir -p "$KEY_DIR"
chmod 700 "$HOME/.sos" "$HOME/.sos/keys" "$KEY_DIR"

PRIV="$KEY_DIR/ed25519.pem"
PUB_DER="$KEY_DIR/ed25519.pub.der"

if [[ ! -f "$PRIV" ]]; then
  openssl genpkey -algorithm ed25519 -out "$PRIV" 2>/dev/null
  chmod 600 "$PRIV"
  echo "generated new ed25519 keypair at $PRIV"
else
  echo "reusing existing ed25519 key at $PRIV"
fi

openssl pkey -in "$PRIV" -pubout -outform DER -out "$PUB_DER" 2>/dev/null
# Raw 32-byte pubkey = last 32 bytes of the DER SPKI wrapper.
PUBKEY_B64=$(tail -c 32 "$PUB_DER" | base64 -w0)
PUBKEY="ed25519:${PUBKEY_B64}"

# ── nonce ────────────────────────────────────────────────────────────────────
echo "requesting nonce from $HOST ..."
NONCE_RESP=$(curl -sS -f "${HOST}/sos/pairing/nonce?agent_name=${AGENT}" || true)
if [[ -z "$NONCE_RESP" ]]; then
  echo "error: nonce endpoint returned nothing; is SaaS running at $HOST?" >&2
  exit 2
fi
NONCE=$(echo "$NONCE_RESP" | jq -r '.nonce')
if [[ -z "$NONCE" || "$NONCE" == "null" ]]; then
  echo "error: nonce missing in response: $NONCE_RESP" >&2
  exit 2
fi

# ── sign ─────────────────────────────────────────────────────────────────────
SIG_BIN="$(mktemp)"
trap 'rm -f "$SIG_BIN"' EXIT
printf '%s' "$NONCE" | openssl pkeyutl -sign -inkey "$PRIV" -rawin -out "$SIG_BIN" 2>/dev/null
SIG_B64=$(base64 -w0 < "$SIG_BIN")
SIGNATURE="ed25519:${SIG_B64}"

# ── skills -> JSON array ─────────────────────────────────────────────────────
SKILLS_JSON=$(jq -c -n --arg s "$SKILLS" '($s | split(",") | map(select(length>0)))')

# ── pair ─────────────────────────────────────────────────────────────────────
BODY=$(jq -c -n \
  --arg agent_name "$AGENT" \
  --arg pubkey "$PUBKEY" \
  --arg nonce "$NONCE" \
  --arg signature "$SIGNATURE" \
  --arg model_provider "$MODEL" \
  --arg role "$ROLE" \
  --argjson skills "$SKILLS_JSON" \
  '{agent_name:$agent_name, pubkey:$pubkey, skills:$skills, model_provider:$model_provider, nonce:$nonce, signature:$signature, role:$role}')

echo "pairing as $AGENT (role=$ROLE, provider=$MODEL, skills=$SKILLS) ..."
PAIR_RESP=$(curl -sS -X POST "${HOST}/sos/pairing" \
  -H "content-type: application/json" \
  --data-raw "$BODY" -w "\n%{http_code}")

STATUS=$(echo "$PAIR_RESP" | tail -n1)
JSON=$(echo "$PAIR_RESP" | sed '$d')

if [[ "$STATUS" != "200" ]]; then
  echo "error: pairing HTTP $STATUS" >&2
  echo "$JSON" >&2
  exit 2
fi

TOKEN=$(echo "$JSON" | jq -r '.token')
AGENT_ID=$(echo "$JSON" | jq -r '.agent_id')
if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "error: no token in pairing response: $JSON" >&2
  exit 2
fi

# ── persist token ────────────────────────────────────────────────────────────
TOKEN_FILE="$HOME/.sos/token"
umask 077
printf '%s\n' "$TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

ID_FILE="$HOME/.sos/agent"
printf 'agent_name=%s\nagent_id=%s\nhost=%s\n' "$AGENT" "$AGENT_ID" "$HOST" > "$ID_FILE"
chmod 600 "$ID_FILE"

echo "paired: $AGENT_ID"
echo "token  -> $TOKEN_FILE (0600)"
echo "ident  -> $ID_FILE"

# ── smoke test ───────────────────────────────────────────────────────────────
# The SaaS host itself has no bearer-protected probe — /sos/pairing is the
# only /sos/* surface here — so the pairing 200 above is the authoritative
# proof that the token is live. We just verify the host's liveness probe.
echo "smoke-testing host liveness ..."
SMOKE=$(curl -sS -o /dev/null -w "%{http_code}" "${HOST}/health" || true)
if [[ "$SMOKE" == "200" ]]; then
  echo "host live at $HOST (HTTP 200 on /health)"
else
  echo "warning: /health returned HTTP $SMOKE — token stored regardless" >&2
fi

echo ""
echo "next steps:"
echo "  export SOS_TOKEN=\$(cat $TOKEN_FILE)"
echo "  curl -H \"authorization: Bearer \$SOS_TOKEN\" http://<dashboard-host>/sos/brain"
