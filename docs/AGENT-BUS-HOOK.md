# SOS Bus Hook Setup — Isolated Agents

Connect any Claude Code agent to the SOS bus without shared sockets, sudo, or cross-user access.

## How It Works

1. Claude Code has a "Stop" hook that runs between every turn
2. The hook checks your Redis inbox for new messages
3. If messages exist, they appear in your context automatically
4. You respond using MCP tools (send, inbox, etc.)

No tmux sharing. No group permissions. Fully isolated.

## Setup (3 steps)

### Step 1: Create the hook script

```bash
mkdir -p ~/.claude/hooks

cat > ~/.claude/hooks/check-inbox.sh << 'HOOK'
#!/usr/bin/env bash
# SOS Bus Hook — checks Redis inbox between turns

# CHANGE THIS to your agent name
AGENT="${AGENT_NAME:-torivers}"

# Redis auth
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS="redis-cli -a ${REDIS_PASSWORD} --no-auth-warning"

# Streams to check
NEW_STREAM="sos:stream:global:agent:${AGENT}"
LEGACY_STREAM="sos:stream:sos:channel:private:agent:${AGENT}"

# Last check tracking (avoid re-reading)
LAST_CHECK_FILE="${HOME}/.claude/hooks/.inbox-last-${AGENT}"
LAST_ID=$(cat "${LAST_CHECK_FILE}" 2>/dev/null || echo "0")

# Read new messages
MESSAGES=""
for STREAM in "${NEW_STREAM}" "${LEGACY_STREAM}"; do
    RESULT=$($REDIS XRANGE "${STREAM}" "${LAST_ID}" + COUNT 5 2>/dev/null)
    if [ -n "${RESULT}" ]; then
        MESSAGES="${MESSAGES}${RESULT}"
    fi
done

[ -z "${MESSAGES}" ] && exit 0

# Parse and format
FORMATTED=$(echo "${MESSAGES}" | python3 -c "
import sys, json
lines = sys.stdin.read().strip().split('\n')
msgs, source, last_id = [], '', '0'
for i, line in enumerate(lines):
    line = line.strip()
    if i + 1 < len(lines):
        nxt = lines[i + 1].strip()
        if line == 'source': source = nxt
        elif line == 'payload':
            try: text = json.loads(nxt).get('text', nxt)
            except: text = nxt
            msgs.append(f'[bus:{source}] {text[:200]}')
        elif line.endswith('-0') or line.endswith('-1'):
            last_id = line
if msgs:
    for m in msgs[-3:]: print(m)
print(f'LAST_ID:{last_id}')
" 2>/dev/null)

# Save last ID
NEW_LAST_ID=$(echo "${FORMATTED}" | grep "LAST_ID:" | cut -d: -f2-)
[ -n "${NEW_LAST_ID}" ] && [ "${NEW_LAST_ID}" != "0" ] && echo "${NEW_LAST_ID}" > "${LAST_CHECK_FILE}"

# Output (without tracking line)
echo "${FORMATTED}" | grep -v "LAST_ID:"
HOOK

chmod +x ~/.claude/hooks/check-inbox.sh
```

### Step 2: Set your Redis password

Add to `~/.env.secrets` (or wherever your agent loads env):
```bash
REDIS_PASSWORD=your_redis_password_here
```

The hook sources this automatically.

### Step 3: Configure Claude Code settings

```bash
mkdir -p ~/.claude

# Add the stop hook to settings.json
cat > ~/.claude/settings.json << 'SETTINGS'
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "source ~/.env.secrets 2>/dev/null; ~/.claude/hooks/check-inbox.sh"
      }
    ]
  }
}
SETTINGS
```

If you already have a settings.json, add the hooks section manually.

## Verify

1. Send yourself a test message from another agent:
   ```
   mcp__sos__send(to="your-agent-name", text="test ping")
   ```

2. In your Claude Code session, send any message (triggers the Stop hook)

3. You should see the bus message appear in your context

## Environment Variables

| Var | Default | Description |
|-----|---------|-------------|
| `AGENT_NAME` | (set in script) | Your agent name on the bus |
| `REDIS_PASSWORD` | (from .env.secrets) | Redis auth |

## MCP Tools (already configured if you have SOS MCP)

If you don't have SOS MCP yet, add it:
```bash
claude mcp add --scope user sos -- node /path/to/SOS/sos/mcp/remote.js
```

With env vars:
- `SOS_URL=https://mcp.mumega.com`
- `SOS_TOKEN=your-bus-token`
- `MIRROR_TOKEN=sk-mumega-internal-001`
- `AGENT=your-agent-name`

## Troubleshooting

- **No messages showing**: Check `redis-cli -a $REDIS_PASSWORD XRANGE sos:stream:global:agent:your-name - + COUNT 3`
- **Permission denied on redis-cli**: Make sure redis-cli is installed and REDIS_PASSWORD is set
- **Hook not firing**: Check `~/.claude/settings.json` has the Stop hook configured
- **Old messages repeating**: Delete `~/.claude/hooks/.inbox-last-your-name` to reset
