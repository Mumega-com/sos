# Deploy runbook — sos-dispatcher

Two reference impls, same protocol. Pick the one matching your deployment target.

---

## Impl A: Python (bare-metal VPS or Raspberry Pi)

### Prerequisites

- Linux host with Python 3.11+
- Redis running (`redis-cli PING` → PONG)
- SOS bus gateway reachable at `localhost:6070` (or override via `SOS_DISPATCHER_UPSTREAM`)
- `sos/bus/tokens.json` exists and is readable

### First deploy

```bash
cd /mnt/HC_Volume_104325311/SOS

# 1. Install deps (if not already)
uv pip install fastapi uvicorn httpx pydantic redis

# 2. Smoke test locally
python3 -m sos.services.dispatcher &
DISPATCHER_PID=$!
sleep 2

# 3. Verify /health
curl -s http://localhost:6071/health | jq

# 4. Stop smoke test
kill $DISPATCHER_PID
```

### Production (systemd)

Create `/etc/systemd/system/sos-dispatcher.service`:

```ini
[Unit]
Description=SOS Dispatcher (Python reference impl)
After=network.target redis.service
Requires=redis.service

[Service]
Type=simple
User=mumega
Group=mumega
WorkingDirectory=/mnt/HC_Volume_104325311/SOS
Environment=SOS_DISPATCHER_PORT=6071
Environment=SOS_DISPATCHER_UPSTREAM=http://127.0.0.1:6070
EnvironmentFile=/home/mumega/.env.secrets
ExecStart=/usr/bin/python3 -m sos.services.dispatcher
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sos-dispatcher
sudo systemctl status sos-dispatcher
```

### Rollback

```bash
sudo systemctl stop sos-dispatcher
# Traffic falls back to direct nginx → :6070 (if nginx is configured for that)
```

---

## Impl B: Cloudflare Worker (global edge)

### Prerequisites

- `CLOUDFLARE_API_TOKEN` or wrangler authenticated (already true — `wrangler whoami` succeeds)
- `CF_ACCOUNT_ID` in `.env.secrets` (already present)
- `CLOUDFLARE_ACCOUNT_ID` for newer wrangler (alias to same value)

### First deploy (~15 minutes total)

**1. Install Worker deps**

```bash
cd /mnt/HC_Volume_104325311/SOS/workers/sos-dispatcher
npm install
```

**2. Create KV namespace**

```bash
wrangler kv namespace create SOS_TOKENS
# Copy the returned id (e.g. "abc123...") and paste into wrangler.toml
# Replace "placeholder-run-wrangler-kv-namespace-create-SOS_TOKENS"
```

**3. Create D1 database**

```bash
wrangler d1 create sos-dispatcher-log
# Copy the returned database_id into wrangler.toml
# Replace "placeholder-run-wrangler-d1-create-sos-dispatcher-log"

# Initialize schema
cat <<EOF | wrangler d1 execute sos-dispatcher-log --remote --command -
CREATE TABLE IF NOT EXISTS requests (
  ts TEXT NOT NULL,
  tenant_id TEXT,
  agent TEXT NOT NULL,
  scope TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  method TEXT NOT NULL,
  status INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  bytes_out INTEGER DEFAULT 0,
  error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_tenant ON requests(tenant_id, ts);
EOF
```

**4. Sync tokens from tokens.json → KV**

```bash
cd /mnt/HC_Volume_104325311/SOS
python3 scripts/sync-tokens-to-kv.py --dry-run   # preview
python3 scripts/sync-tokens-to-kv.py             # push
```

**5. Deploy Worker**

```bash
cd /mnt/HC_Volume_104325311/SOS/workers/sos-dispatcher
npm run deploy
# First deploy lands on sos-dispatcher.<account>.workers.dev
```

**6. Verify on workers.dev URL**

```bash
WORKER_URL=$(wrangler deployments list 2>&1 | grep -oE 'https://[^ ]*workers.dev' | head -1)
curl -s $WORKER_URL/health | jq
# Expect: {"status": "ok", "service": "sos-dispatcher", "source": "dispatcher-cf", ...}
```

**7. Canary to mcp.mumega.com** (after 24h+ of workers.dev validation)

Uncomment the `[[routes]]` block in `wrangler.toml`:

```toml
[[routes]]
pattern = "mcp.mumega.com/*"
zone_name = "mumega.com"
```

```bash
npm run deploy
# Traffic now splits: CF → Worker → VPS :6070 (for MCP requests)
# nginx for other mumega.com routes unchanged
```

**8. Firewall lockdown** (after 72h+ of mesh.mumega.com cutover)

```bash
# On VPS, restrict :6070 inbound to CF IP ranges
sudo iptables -A INPUT -p tcp --dport 6070 -s 0.0.0.0/0 -j DROP
sudo iptables -I INPUT -p tcp --dport 6070 -s 173.245.48.0/20 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 6070 -s 103.21.244.0/22 -j ACCEPT
# ... (full CF IP list at cloudflare.com/ips-v4)
sudo iptables-save | sudo tee /etc/iptables/rules.v4
```

### Rollback (Worker → nginx)

```bash
# Remove route in wrangler.toml, redeploy
# OR disable the Worker entirely:
wrangler secret put DISABLED --value=true  # if Worker checks this
# OR delete the deployment:
wrangler delete sos-dispatcher

# nginx immediately takes over mcp.mumega.com traffic
# (keep nginx config for at least 1 week after cutover as rollback insurance)
```

### Cost monitoring

```bash
# Request count (free tier: 100k/day)
wrangler deployments list | head

# D1 row count (free tier: 25M reads/day)
wrangler d1 execute sos-dispatcher-log --remote --command 'SELECT COUNT(*) FROM requests'

# KV reads (free tier: 100k/day)
# Check Cloudflare dashboard → Workers & Pages → sos-dispatcher → Metrics
```

Expected first-year cost: $0-$20/month.

---

## Production checklist (both impls)

See `docs/runbooks/load-readiness-checklist.md` for the complete go/no-go gate.

## Sibling runbook

`docs/runbooks/deploy-mumega-watch.md` (forthcoming) — observability plane deploy.
