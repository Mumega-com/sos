# SOS Roadmap — v0.4 → v1.0

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Canonical (supersedes fragments from 2026-04-16 & earlier)
**Aligns with:** Mumega product phases in `mumega-docs:vision/roadmap.md`

---

## One-page version (updated 2026-04-17 with shipped state)

```
  v0.4.0-alpha.2  ✅ shipped 2026-04-17  Agent Card + 8 message schemas + enforcement
  v0.4.0-beta.1   ✅ shipped 2026-04-17  MCP send migrated to v1 "send" type
  v0.4.0          ✅ shipped 2026-04-17  all legacy producers → v1 + strict enforcement (SOS-4004)
  v0.4.0.x        🟡 deferred            error-taxonomy completion + OpenAPI generation (shaped but not filed)
  v0.4.1  Provider Matrix    — LLM routing independence, closes OpenClaw-risk class
  v0.4.2  Observability      — external watchdog on 2nd VPS (CF Worker path dropped per Hadi; Mesh remains available for mesh membership)
  v0.4.3  Dispatcher         — Python on VPS (CF Worker deferred — SOS stays CF-agnostic at kernel layer)
  v0.5.0  Traceable          — OpenTelemetry end-to-end
  v0.6.0  Migratable         — Alembic migrations, idempotency keys, feature flags
  v0.7.0  Isolated           — adapters run as separate services, not in-process imports
  v0.8.0  Capable            — capability auth enforced, error taxonomy complete
  v0.9.0  Frozen             — contract freeze, no breaking changes without 2.0
  v1.0.0  Ship               — Rust kernel (summer target), Python reference remains
```

**Hadi directive 2026-04-17 evening:** close SOS + Mirror public repos; Mumega goes
Mycelium Network (public junction, sovereign nodes, $MIND economy). Federation + mesh use Cloudflare
opportunistically but the kernel stays bare-metal-deployable. Rust port target:
late June / early July 2026. Private repos preserve git history; OSS extraction
revisited at v1.0 or later.

Each version is a shippable increment. Each one ships more determinism than the last. The last three are OSS-maturity moves; the first four are in-flight product enablement.

---

## Mapping to Mumega product phases

| Mumega Phase | Status | Needs which SOS versions |
|---|---|---|
| Phase 0 Foundation | done | pre-v0.4 (kernel, bus, squad, mirror, wake daemon, calcifer, lifecycle, code-review-graph) |
| Phase 1 Product | done | pre-v0.4 (Inkwell v5, SaaS :8075, Stripe, Resend, mumega-edge, Glass Commerce) |
| Phase 2 Wire & Clean (current) | 12 items open | **v0.4.0 Contracts** (typed endpoints so Kasra's /my/* work has a frozen API shape) + **v0.4.1 Provider Matrix** (removes OpenClaw single-point-of-failure) |
| Phase 3 Test (mock customers E2E) | next | **v0.4.2 Observability** (can't verify isolation + build queue + webhook delivery without it) |
| Phase 4 Dogfood (GAF, DNU, TROP internal) | planned | **v0.4.3 Dispatcher + Code Mode** (per-project rate limits + 99.9% token savings for internal dogfooding on real budget) |
| Phase 5 Go Viral (GitHub launch, HN, ToRivers public) | future | **v0.9.0 Frozen** (can't open-source a moving target; frozen API is prerequisite for "fork Inkwell" pattern) |
| Phase 6 Scale ($MIND mainnet, 100+ workers) | far | **v1.0 + post-1.0** ($MIND Solana work, Workers for Platforms, human worker onboarding) |

Every Mumega phase gate is an SOS version gate. This is why v0.4 isn't optional — Phase 2 is *current* and it depends on it.

---

## What each version ships

### v0.4.0 "Contracts" — typed reality ✅ **shipped 2026-04-17** (commit `0e3afefb`)

**Problem it solved:** Silent schema drift. "Works most of the time" fields. The SEC-001 regression class. Flat identity. Free-text errors.

**Delivered:**
1. ✅ Agent Card v1 JSON Schema + Pydantic + contract tests (commit `2c0469c1`, 10/10)
2. ✅ Message schema registry — 8 types (announce, send, wake, ask, task_created, task_claimed, task_completed, agent_joined) with `source` structurally required
3. ✅ Pydantic `BusMessage` base + 8 subclasses + `to_redis_fields()` / `from_redis_fields()` round-trip
4. ✅ Enforcement module with 4 error codes (SOS-4001/4002/4003/4004)
5. ✅ All 4 bus producers migrated to v1 (bridge, sos_mcp, sos_mcp_sse, redis_bus)
6. ✅ 56 contract tests green
7. ✅ `v0.4.0` tag + CHANGELOG current

**Deferred to v0.4.0.x (not blocking Provider Matrix):**
- OpenAPI 3.1 per service (8 services) — needed for cross-service contract tests but not runtime correctness
- Complete SOS-XXXX error taxonomy beyond the 4xxx bus-validation codes already shipped

**Squad:** Haiku executors (sos-schema-author, sos-pydantic-author, sos-openapi-author, sos-contract-tester) + Opus advisor (Athena) at commit gates. Sprint budget ~$3.

**Side effect:** flat-identity class (Mumega-docs #20, #21, #22) closes by construction when message schemas enforce `source`.

**Link:** `docs/plans/2026-04-16-sos-v0.4-contracts.md` + `docs/plans/2026-04-16-sos-v0.4-sprint-squad.md`

---

### v0.4.1 "Provider Matrix" — LLM independence (2 weeks after v0.4.0)

**Problem it solves:** OpenClaw single-point-of-failure (today's outage: all 6 openclaw-hosted agents died). No SOS-native LLM fallback. Adapter pricing tables stale (Mumega-com/sos#97).

**Deliverables:**
1. Provider Card v1 schema (same pattern as Agent Card — JSON Schema + Pydantic + tests)
2. Redis health table `sos:provider:{name}` with 60s probe cron
3. Deterministic provider selection: `select(required_tier, budget) → cheapest healthy`
4. Per-provider circuit breaker with exponential backoff
5. Pre-expiry OAuth refresh (no 401-waterfall loops)
6. `~/.sos/config/providers.yaml` — fallback chain as config, not hardcoded
7. FMAAP Metabolism pillar extension — no healthy provider → SOS-5001, no silent cascade

**Urgency driver:** OpenClaw is an external tool. Today's outage is upstream bug #56960, not ours to fix. SOS needs its own provider routing so "OpenClaw down" is not "entire org down."

**Closes:** Mumega-com/sos#97 by construction (Provider Cards carry current pricing). Unblocks Mumega-com/therealmofpatterns#140 (TROP token ledger needs fresh pricing to compute cost).

**Link:** `docs/plans/2026-04-17-openclaw-sos-boundary.md` (why); Mumega-com/sos#100 (what).

---

### v0.4.2 "Observability" — external watchdog (2 weeks after v0.4.1)

**Problem it solves:** Everything that watches SOS runs ON SOS. VPS down = no alarm because the alarmer is also down. Today's OpenClaw outage went silent until someone tried to use it.

**Deliverables:**
1. `breakables.yaml` single source of truth (Breakable Card schema — JSON Schema + Pydantic + tests)
2. `mumega-watch` Cloudflare Worker with 60s Cron Trigger probing every breakable from outside the VPS
3. Results written to CF KV (status) + D1 (incident history) + Durable Object Facets (per-breakable timeline as SQL)
4. `status.mumega.com` public status page (CF Pages, read-only)
5. Bus event `sos:event:breakable_down` + agent propagation (Calcifer reacts in-VPS, sos-medic opens incident)
6. Escalation ladder: Discord → Telegram (5min) → phone/Twilio (30min) → auto-restart attempt (2h)
7. `sos status` CLI that reads from CF KV (works even when VPS is down)

**Cloudflare integration:**
- Mesh itself is a first-class breakable (if Mesh is down, all scoped-access agents lose private-network reach)
- DO Facets give per-tenant/per-breakable SQLite history at the edge — strengthens the plan

**Link:** `docs/plans/2026-04-16-sos-v0.4-contracts.md` references v0.4.2 in the section-after-next; full plan pending.

---

### v0.4.3 "Dispatcher + Code Mode" — edge front door (2-3 weeks after v0.4.2)

**Problem it solves:** `mcp.mumega.com` is a dumb nginx proxy with no edge validation, no rate limit, no revocation path shorter than "edit tokens.json + restart MCP." Shared `sk-claudeai-*` token causes flat identity.

**Rescoped after Cloudflare Agents Week (Apr 13-17):**

Originally: custom CF Worker with token validation, rate limiting, revocation. ~300 LOC.

With CF Mesh in play: thin pass-through INSIDE a Mesh-protected network. Mesh owns agent identity + private-network routing. Dispatcher owns SOS bus-token validation + SOS-specific rate limits. ~100 LOC.

**Added scope:**
- **Code Mode MCP pattern** — 99.9% token reduction for MCP tool calls. Cloudflare open-sourced the SDK. Fold this into v0.4.3 rather than defer — it's the biggest single token-economy win available.

**Deliverables:**
1. `workers/sos-dispatcher/` — CF Worker source (TypeScript, Hono, KV + D1 + DO bindings)
2. Code Mode MCP wrapper for `mcp__sos__*` tools — preserves SSE + streamable-HTTP transports while slashing token cost per call
3. `scripts/sync-tokens-to-kv.py` — sync from `tokens.json` to CF KV (source of truth stays on VPS)
4. `wrangler.toml` with Mesh bindings (if v0.4.5 Mesh adoption greenlit)
5. VPS firewall lockdown (`iptables` restricts `:6070` to CF IP ranges only)
6. Rollout: alpha (transparent proxy, parallel with nginx) → beta (rate limits) → release (revocation + analytics) → rc (firewall lockdown + nginx retirement)

**Naming cleanup:** retire `sk-claudeai-*` token family, rename `claude.ai sos-claude` → `sos` or `sos-dispatcher`. "claude.ai" was always a misnomer — dispatcher serves any MCP-capable client.

**Link:** `docs/plans/2026-04-17-claude-dispatcher.md` (full); Mumega-com/sos#101 (issue).

---

### v0.5.0 "Traceable" — observability deepens (3-4 weeks after v0.4.3)

**Problem it solves:** You can't prove determinism without traces. A Rust port (post-1.0) isn't verifiable without a contract test harness.

**Deliverables:**
1. OpenTelemetry integration in every service — single trace ID flows through bus + HTTP + MCP calls
2. Trace → Mirror engram link (every significant trace writes a reference engram for long-term recall)
3. CF Workers analytics integration (edge events correlate with VPS traces)
4. Test replay harness — capture production traces, replay in test, assert same outcomes
5. Dashboard at `app.mumega.com/sos` (the engine dashboard plan — ops view) consumes trace data

**Link:** `docs/plans/2026-04-16-sos-engine-dashboard.md`.

---

### v0.6.0 "Migratable" — schema + config discipline

**Deliverables:**
1. Alembic for SQLite (squads.db) + pgvector (Mirror) — versioned migrations, one authoritative tool
2. Feature flag framework — dark-launch new endpoints, A/B rollouts at the dispatcher layer
3. Idempotency keys on every write endpoint (Provider Cards, Agent Cards, signups, onboard, task_create)
4. Single typed config struct replaces `.env.secrets + tokens.json + agent_registry.py` sprawl
5. Secret rotation script + `secrets-rotate.sh` runbook

---

### v0.7.0 "Isolated" — adapters split out

**Deliverables:**
1. Every adapter currently in `sos/adapters/*` moves to its own process + systemd unit + HTTP endpoint
2. SOS core stops importing adapter modules — they're clients of core APIs
3. Enables selective Rust-porting of specific adapters (Discord → serenity, Telegram → teloxide) while keeping CrewAI / LangGraph / Vertex ADK Python
4. Per-adapter health surfaces in observability plane

---

### v0.8.0 "Capable" — capability auth enforced

**Deliverables:**
1. `sos/kernel/capability.py` moves from advisory to enforced on every service endpoint
2. Ed25519-signed capabilities replace plain Bearer tokens for cross-service calls
3. FMAAP Autonomy pillar checks capability signatures
4. Error taxonomy (v0.4.0 groundwork) complete — every raiseable path has a code
5. Audit log captures capability grants + exercises

---

### v0.9.0 "Frozen" — contract freeze

**Deliverables:**
1. Every public API frozen at current shape. Breaking changes now require 2.0.
2. Deprecation policy documented (N=2 minor releases before removal, always)
3. Full SDK (Python + TypeScript) generated from OpenAPI specs
4. API changelog becomes a compatibility log (what changed, since-when, how to migrate)
5. CI rule: any change to a frozen contract requires explicit `breaking-change` label + maintainer approval

**Why this matters:** Phase 5 Go Viral (public launch, HN) requires stability promises SOS can keep. A 0.x kernel can't promise that. A 1.0 kernel with a frozen contract can.

---

### v1.0.0 "Ship" — dual-mode deployment

**Deliverables:**
1. Two tested deployment modes:
   - **Mode 1 (CF-agnostic / OSS):** bare-metal Linux + Redis + nginx + Python services. For public forks.
   - **Mode 2 (CF-native / Mumega production):** Mesh + Workers + KV + D1 + DO Facets. For Mumega's own deployment.
2. Same kernel code; abstraction lives at the ingress (dispatcher) and persistence (storage) layers only
3. Public GitHub launch of `Mumega-com/sos`
4. HN post + blog series
5. SDK published on PyPI + npm
6. Fork guide for "build your own Mumega" on another infra

**At this point OpenClaw is optional** — openclaw-hosted agents (athena, sol, etc.) have been migrated per the OpenClaw migration plan (`docs/plans/2026-04-17-openclaw-migration.md`). New forks of SOS don't need OpenClaw.

---

## How today's findings reshape the roadmap

| Finding | Roadmap impact |
|---|---|
| **OpenClaw upstream bug #56960** | v0.4.1 urgency confirmed — SOS needs LLM independence |
| **OpenClaw is external (not ours)** | v0.5-v0.7 gain explicit agent migration path — not optional, scheduled |
| **CF Mesh (Apr 14) — per-agent identity + private network** | v0.4.3 rescoped to thin pass-through on Mesh; v1.0 dual-mode commitment |
| **CF Code Mode MCP (Apr 13) — 99.9% token reduction** | Folded into v0.4.3; biggest token-economy win this year |
| **CF DO Facets — per-instance SQLite** | Strengthens v0.4.2 (observability) + aligns with Clockwork squad-per-db target |
| **CF Sandboxes GA** | Future replacement for per-tenant Linux user pattern; scheduled for v0.5-v0.7 in the OpenClaw migration |
| **CF Agents SDK v2 preview (Project Think)** | Evaluated in v0.5-v0.6; kernel stays independent; Mumega deployment can adopt |

## Sprint cadence

Each v0.4.x release: **2-4 weeks**.
Each v0.5+ release: **3-6 weeks**.
End-to-end v0.4 → v1.0: **estimated 8-12 months at current pace**.

Accelerators:
- Haiku+Opus subagent squad (~$3/sprint)
- Cloudflare primitives (free managed services replace weeks of custom work)
- Code Mode MCP (99.9% token reduction compounds across every future tool call)

Decelerators:
- Kasra's SaaS-side boundaries (I can't touch `/my/*` implementations)
- OpenClaw ongoing maintenance drag (each upstream bug = unplanned work)
- Decision latency on architectural choices (α/β/γ, OSS repo split, etc.)

## Version bumps require

1. **Patch (0.4.x)** — bug fix in a shipped contract, no spec change
2. **Minor (0.x.0)** — new contract shipped, old contracts still valid
3. **Major (x.0.0)** — breaking change to an existing contract; only allowed pre-1.0 and at major-version bumps after

## Decisions that lock this roadmap

All open, blocks no immediate work but the answers reshape priorities:

| # | Decision | Default if Hadi silent |
|---|---|---|
| CF-α | Aggressive CF adoption (Mesh + Code Mode + DO Facets + Sandboxes) | Default: yes, per Hadi's 2026-04-17 "if CF is better lets go" response |
| OC-C | OpenClaw strategy — learn patterns, ship shallow SOS-native equivalents | Default: yes, per `2026-04-17-openclaw-sos-boundary.md` |
| OSS-split | When to extract public SOS repo | Default: at v0.9 Frozen — no sooner |
| $MIND-P6 | $MIND work is Phase 6 — not in v0.4-v1.0 scope | Default: yes, per Mumega roadmap |
| Dispatcher-rollout | Parallel to nginx for one week before cutover | Default: yes, conservative rollout |

## Links

- **Current sprint:** `docs/plans/2026-04-16-sos-v0.4-contracts.md`, `docs/plans/2026-04-16-sos-v0.4-sprint-squad.md`
- **Scope line:** `docs/plans/2026-04-16-sos-dev-scope-and-mumega-boundary.md`
- **Identity rollout:** `docs/plans/2026-04-16-identity-rollout.md`
- **Engine dashboard:** `docs/plans/2026-04-16-sos-engine-dashboard.md`
- **Dispatcher plan:** `docs/plans/2026-04-17-claude-dispatcher.md`
- **OpenClaw boundary:** `docs/plans/2026-04-17-openclaw-sos-boundary.md`
- **CF Agents Week:** `docs/plans/2026-04-17-cloudflare-agents-week-context.md`
- **OpenClaw migration:** `docs/plans/2026-04-17-openclaw-migration.md` (sibling, forthcoming)
- **Code Mode adoption:** `docs/plans/2026-04-17-code-mode-mcp-adoption.md` (sibling, forthcoming)
- **OSS extraction:** `docs/plans/2026-04-17-oss-extraction.md` (sibling, forthcoming)
- **$MIND status:** `docs/plans/2026-04-17-mind-status-and-integration.md` (sibling, forthcoming)
- **Squad-graph absorption:** `docs/plans/2026-04-17-squad-living-graph-v04-absorption.md` (sibling, forthcoming)
- **Mumega product phases:** `Mumega-com/mumega-docs:vision/roadmap.md`

## One-line summary

Ten versions from here to 1.0; each one ships more determinism and more schema discipline; Cloudflare primitives absorbed where they replace work SOS would otherwise build; OpenClaw migrated off by v1.0; $MIND mainnet and 1M-squad scale live in Phase 6 / post-1.0. Ship cadence: 2-6 weeks per version, 8-12 months to v1.0.
