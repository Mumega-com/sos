# SOS load-readiness checklist

**Purpose:** pre-production go/no-go for handling real customer traffic.
**Owner:** sos-dev
**When to run:** before cutover to dispatcher, before onboarding a new tier of customers, quarterly as a baseline

Answer each as YES / NO / N/A. If any YES-required item is NO, block the cutover.

## A. Identity & auth

- [ ] Per-agent identity enforced — every bus message carries correct `source` (flat-identity bug closed)
- [ ] Token hashes in `tokens.json` match what dispatcher/KV expects (run `scripts/sync-tokens-to-kv.py --dry-run` and confirm zero drift)
- [ ] No active `sk-claudeai-*` shared tokens (retired as part of dispatcher rollout)
- [ ] Revocation path tested: mark a token inactive, wait 60s, confirm dispatcher rejects

## B. Rate limiting

- [ ] Plan tiers configured (starter=10 rpm, growth=100 rpm, scale=1000 rpm, enterprise=unlimited)
- [ ] 429 returned with `SOS-9003` + `Retry-After` header
- [ ] Per-tenant isolation verified (tenant A hitting 10 rpm doesn't affect tenant B)
- [ ] Admin/internal tokens exempted from rate-limit (tenant_id=null)

## C. Circuit breakers

- [ ] Provider Matrix has circuit breaker wired (v0.4.1 — if not shipped, no-go for non-critical providers)
- [ ] Upstream `:6070` timeout returns `SOS-5xxx`, not a cascade
- [ ] Dispatcher fail-open on Redis/KV outage (don't block traffic on observability backend)

## D. Observability

- [ ] `mumega-watch` probes running every 60s (v0.4.2 — if not shipped, no-go for production)
- [ ] Every breakable in `breakables.yaml` currently green OR explicitly in-known-failure
- [ ] Discord alerts configured for `severity: critical` breakables
- [ ] Escalation ladder functional (Discord → Telegram → phone if Twilio available)
- [ ] `status.mumega.com` live with current state

## E. Accounting & attribution

- [ ] `~/.claude/hooks/token-accounting.sh` fails loudly on missing `AGENT_NAME` (no silent kasra default)
- [ ] Every session's tmux launch exports `AGENT_NAME=<name>` before `claude`
- [ ] Accounting ledger shows >1 distinct agent for any 24h window (if all rows are kasra, attribution broken)

## F. Storage headroom

- [ ] Disk `/` < 85% (target). Today sits at 93% — cleanup blocks anything.
- [ ] Disk `/mnt/HC_Volume_104325311` < 85%
- [ ] Redis memory < 50% of max (verify with `redis-cli INFO memory`)
- [ ] `squads.db` size reasonable (< 100MB for now; Alembic migrations needed if rapid growth)
- [ ] pgvector `mirror` DB size tracked (> 1GB gets expensive, plan pruning)

## G. Backup cadence

- [ ] `tokens.json` backed up (`tokens.json.backup` exists, last < 24h)
- [ ] `squads.db` backed up daily (cron or manual cadence documented)
- [ ] Redis AOF or RDB persistence enabled (verify `redis-cli CONFIG GET appendonly`)
- [ ] pgvector backup — supabase's or our own

## H. Rollback readiness

- [ ] Dispatcher can be bypassed by nginx (keep old config as backup for 1 week after cutover)
- [ ] `tokens.json` changes reversible (backup before any write)
- [ ] Latest git commit on prod branch is tagged (can `git reset --hard <tag>` to recover)
- [ ] Known-good customer test token exists and is documented (can verify end-to-end after any change)

## I. Upstream provider health

- [ ] Primary LLM provider (Anthropic) API key not near expiry
- [ ] At least 2 fallback providers configured with valid creds
- [ ] OpenClaw upgraded past bug #56960 OR openclaw-hosted agents are explicitly degraded-accepted
- [ ] Mumega billing alert wired (Stripe threshold + LLM cost cap)

## J. Secrets & config

- [ ] `.env.secrets` file permissions 600, owned by mumega:mumega
- [ ] No secrets in git history (verify with `git log -p | grep -iE 'api_key|secret_key|token.*[a-f0-9]{40,}'`)
- [ ] CF API token has exactly the scopes listed in dispatcher README (not over-privileged)
- [ ] `wrangler` authenticated with the right account (run `wrangler whoami`)

## K. Contract integrity

- [ ] `tests/contracts/` passes 100% (v0.4.0 Contracts green)
- [ ] Dispatcher passes `tests/contracts/test_dispatcher_protocol.py` (both Python and CF impls if both deployed)
- [ ] No schema drift between `tokens.json` and CF KV (confirm with sync --dry-run)
- [ ] OpenAPI specs match live service responses (run a live diff)

## L. Customer impact scenarios

Walk through each in a pre-cutover tabletop:

- [ ] What happens when CF Mesh is down? (dispatcher falls back to direct VPS or refuses — must be defined)
- [ ] What happens when dispatcher returns 500? (client sees? customer sees? retry policy?)
- [ ] What happens when Redis is down? (bus inbox unavailable — fail mode?)
- [ ] What happens when `tokens.json` is corrupted? (dispatcher fails closed? manual recovery?)
- [ ] What happens when a customer hits their rate limit mid-conversation? (UX?)
- [ ] What happens when Anthropic API is rate-limiting at 100%? (cascades, gradual degrade, or hard stop?)

## Verdict

```
Total YES required items NO: ___

If 0: GO for cutover
If 1-3: Pause, resolve each before scheduling
If >3: Back to sprint — prerequisites not met
```

## History

- 2026-04-17 — Checklist created. First full run TBD after v0.4.2 observability + v0.4.3 dispatcher ship.
