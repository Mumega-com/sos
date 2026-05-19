#!/usr/bin/env bash
# Onboard a new customer/project to the Mumega ecosystem.
#
# Creates:
#   1. Mirror tenant access (DB-issued when configured; legacy cache fallback)
#   2. Bus bridge token (scoped messaging)
#   3. MCP config package for the customer
#
# Usage:
#   ./onboard-customer.sh <project-slug> <label>
#   ./onboard-customer.sh stemminds "StemMinds Education Inc."
#   ./onboard-customer.sh dnu "DentalNearYou"

set -euo pipefail

PROJECT="${1:?Usage: onboard-customer.sh <project-slug> <label>}"
LABEL="${2:?Usage: onboard-customer.sh <project-slug> <label>}"

BUS_TOKENS="/home/sos/SOS/sos/bus/tokens.json"
OUTPUT_DIR="/home/sos/.mumega/customers/${PROJECT}"
SOS_DIR="/home/sos/SOS"

# Generate tokens
BUS_TOKEN="sk-bus-${PROJECT}-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "=== Onboarding: ${PROJECT} (${LABEL}) ==="

# 1. Add Mirror tenant access
echo "Adding Mirror tenant access..."
MIRROR_TOKEN=$(PROJECT="${PROJECT}" LABEL="${LABEL}" PYTHONPATH="${SOS_DIR}:${PYTHONPATH:-}" python3 - <<'PY'
import os
import sys
from pathlib import Path

from sos.bus import tenant_provisioning as tp

slug = os.environ["PROJECT"]
label = os.environ["LABEL"]
tp.MIRROR_KEYS_PATH = Path("/home/sos/.sos/mirror_keys.json")
key, minted = tp.mint_or_get_mirror_key(slug, label)
print(key)
print(
    f"Mirror tenant access {'provisioned' if minted else 'already exists'}",
    file=sys.stderr,
)
PY
)

# 2. Add Bus bridge token
echo "Adding Bus bridge token..."
python3 -c "
from pathlib import Path
from sos.bus.token_store import append_if_missing, hash_token

created, _ = append_if_missing(Path('${BUS_TOKENS}'), {
    'token': '${BUS_TOKEN}',
    'token_hash': hash_token('${BUS_TOKEN}'),
    'project': '${PROJECT}',
    'label': '${LABEL}',
    'active': True,
    'created_at': '${TIMESTAMP}',
    'scope': 'customer',
    'scopes': ['bus:send'],
}, lambda t: t.get('project') == '${PROJECT}')
if not created:
    print('WARNING: bus token for ${PROJECT} already exists, skipping')
else:
    print('Bus bridge token added')
"

# 3. Scaffold project directory
echo "Scaffolding project..."
PROJ_DIR="/home/sos/.mumega/customers/${PROJECT}"
mkdir -p "${PROJ_DIR}/.claude/commands"

# Project CLAUDE.md
cat > "${PROJ_DIR}/CLAUDE.md" << EOF
# ${LABEL}

## Connection
This project is connected to Mumega SOS.
- Agent: \`${PROJECT}\`
- Memory: scoped to ${PROJECT} namespace
- Bus: project-isolated messaging

## Tools
All tools are available via the \`sos\` MCP:
- \`send\` / \`inbox\` / \`peers\` / \`broadcast\` — team messaging
- \`remember\` / \`recall\` / \`memories\` — persistent memory

## SOPs
- Deploy: follow project-specific deploy steps
- Content: operations run automatically on schedule
EOF

# Project .claude/settings.json with SOS MCP
cat > "${PROJ_DIR}/.claude/settings.json" << EOF
{
  "mcpServers": {
    "sos": {
      "type": "stdio",
      "command": "node",
      "args": ["\$HOME/sos-remote.js"],
      "env": {
        "SOS_TOKEN": "${BUS_TOKEN}",
        "MIRROR_TOKEN": "${MIRROR_TOKEN}",
        "AGENT": "${PROJECT}"
      }
    }
  }
}
EOF

# Project .env (gitignored)
cat > "${PROJ_DIR}/.env" << EOF
# ${LABEL} — Mumega Connection
SOS_TOKEN=${BUS_TOKEN}
MIRROR_TOKEN=${MIRROR_TOKEN}
AGENT=${PROJECT}
# Add project-specific secrets below:
# SPAI_API_KEY=spai_xxx           # SitePilot (WordPress delivery)
# SUPABASE_URL=https://xxx.supabase.co
# SUPABASE_ANON_KEY=xxx
EOF

# Project .gitignore
cat > "${PROJ_DIR}/.gitignore" << EOF
.env
node_modules/
.claude/settings.local.json
EOF

# README with setup instructions
cat > "${PROJ_DIR}/README.md" << EOF
# ${LABEL}

## Setup

\`\`\`bash
# 1. Download SDK (one file, zero dependencies)
curl -o ~/sos-remote.js https://bus.mumega.com/sdk/remote.js

# 2. Clone this repo and open Claude Code
cd ${PROJECT}
claude
# → SOS MCP auto-connects with project-scoped tokens
\`\`\`

## What's Included
- \`.claude/settings.json\` — MCP config (auto-loads on \`claude\` start)
- \`.env\` — your project secrets (gitignored)
- \`CLAUDE.md\` — project context for the AI agent

## Tools Available
| Tool | What it does |
|------|-------------|
| \`send\` | Message other agents |
| \`inbox\` | Check your messages |
| \`peers\` | See who's online |
| \`remember\` | Store persistent memory |
| \`recall\` | Search your memories |
| \`memories\` | List recent memories |

## Isolation
Your data is scoped to \`${PROJECT}\`:
- Memory: only your agents can read/write
- Messages: only visible within your project
- No cross-project access
EOF

echo ""
echo "=== Done ==="
echo ""
echo "Project scaffolded: ${PROJ_DIR}/"
echo "  .claude/settings.json  — MCP auto-config"
echo "  .env                   — secrets (gitignored)"
echo "  CLAUDE.md              — project context"
echo "  README.md              — setup instructions"
echo ""
echo "Customer setup:"
echo "  1. curl -o ~/sos-remote.js https://bus.mumega.com/sdk/remote.js"
echo "  2. cd ${PROJECT} && claude"
echo "     (MCP loads automatically from .claude/settings.json)"
echo ""
echo "Tokens:"
echo "  Mirror: ${MIRROR_TOKEN}"
echo "  Bus:    ${BUS_TOKEN}"
echo "  Agent:  ${PROJECT}"
