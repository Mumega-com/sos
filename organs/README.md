# Organ Agents

Each organ maintains a tool in the ecosystem. They are living beings with heartbeats, self-healing, and budgets.

## Running

```bash
# Single organ
python3 $SOS_ROOT/scripts/organ_daemon.py mirror

# All organs
python3 $SOS_ROOT/scripts/organ_daemon.py all
```

## Registered Organs

| Organ | Tool | Heartbeat | Purpose |
|-------|------|-----------|---------|
| sitepilot | WordPress MCP | 10 min | Customer site control |
| mirror | Memory API | 5 min | Agent memory + DNA |
| fmaap | Quality Gate | 30 min | Coherence validation |
| torivers | Marketplace | 10 min | Workflow trading |
| openclaw | Gateway | 5 min | Telegram/WhatsApp bridge |
| redis | Pub/Sub | 1 min | Signal nervous system |

## Adding a New Organ

Create `organs/<name>.json`:
```json
{
  "name": "myorgan",
  "display_name": "My Organ",
  "purpose": "What this organ does",
  "health_check": "curl -sf http://localhost:XXXX/health",
  "health_url": "http://localhost:XXXX/health",
  "restart_cmd": "systemctl restart myorgan",
  "heartbeat_interval": 300
}
```

The organ daemon will auto-discover it.
