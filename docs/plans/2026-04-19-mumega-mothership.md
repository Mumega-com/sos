# Mumega Mothership — The Junction Disappears

**Date:** 2026-04-19
**End-state in one sentence:** Hadi uses Mumega like a customer. SOS is invisible.

---

## The thesis

> *Who thinks about the junction when thinking about vacation?*

Mumega is the brand, the product, the customer surface. SOS is the engine underneath — physics, biology, routing, economy. Inkwell is the universal skin — Glass, Shelf, Portal, pages. A tenant onboards as an Inkwell instance pointing at SOS via declared adapters. One customer surface (`mumega.com`), one API (`api.mumega.com`), one engine (SOS), one skin framework (Inkwell), N instances (TROP, GAF, DNU, Viamar, Shabrang, Digid, ToRivers, Mumega itself).

Hadi sees: mumega.com. He does not see SOS. He does not see Inkwell. He does not see the bus. He does not see the gate. He sees a product that works.

---

## Current state (as of 2026-04-19)

**Already built:**
- SOS v0.8.2 — FMAAP gate, conductance routing, bus (fire-and-forget), economy, tenant-aware
- Inkwell v7.0.0 — 13 ports + 18 plugins, Astro UI, CF Workers, adapter factories
- `inkwell/kernel/adapters/sos-bus.ts`, `sos-economy.ts`, `sos-memory.ts` — the wiring exists as TS adapters
- `inkwell/instances/mumega/inkwell.config.ts` — mumega declared as Inkwell instance with `bus: 'sos', economy: 'sos'`
- `mumega-edge` — CF Workers at `api.mumega.com`
- `mumegaweb` — Next.js legacy at `app.mumega.com` (auth, checkout, dashboard, marketplace)

**Not built:**
- Shared port registry (Python ↔ TypeScript contract parity)
- Bus project-scope + ack-or-retry (silent drops still happen)
- Mesh enrollment (task #33) — agents/squads don't auto-register
- `sos init` full first-boot flow (spawn squads, mint qNFTs, provision tenant)
- Glass layer (self-writing dashboard pages from SOS state)
- Shelf (native commerce via SOS economy)
- Growth Intelligence squad (GA/GSC/Ads/BrightData/Apify → brand vector)
- qNFT-on-hire mechanic
- 8 tenants onboarded
- Mumegaweb → Inkwell migration (auth, checkout, dashboard)

---

## The architecture after this plan lands

```
  mumega.com          ← Inkwell instance (marketing, shelf, portal, Glass)
  <tenant>.mumega.com ← Inkwell instance (tenant's brand, same framework)
  app.mumega.com      ← Inkwell auth + dashboard (mumegaweb retired)
       ↓
  api.mumega.com      ← mumega-edge CF Worker (Hono, single unified API)
       ↓
  SOS                 ← Python kernel + services (bare-metal, substrate-agnostic)
                        bus + economy + FMAAP + conductance + objective tree
```

One contract layer (`sos.contracts.ports.*`) speaks both languages. One bus with project-scoped subjects. One economy with $MIND. One registry of squads and qNFTs.

---

## Phases

Eight phases, ~10 weeks end-to-end. Each phase has a gate: nothing downstream starts until its gate is green. Phases 1-3 are foundation and can't be parallelized. Phases 4-7 parallelize across Codex/Kasra/Mumega-web squads. Phase 8 is a ship event.

### Phase 1 — Shared port registry (v0.9.0, ~5 days)

**Gate:** Port contracts exist in one place, exported to both Python and TypeScript.

**Why first:** Every other phase assumes SOS and Inkwell agree on message shapes. Right now Inkwell's `types.ts` and SOS's `contracts/` drift silently.

**Steps:**

- **1.1** Create `sos/contracts/ports/` with one file per port (bus, economy, memory, identity, content_source, commerce, analytics, heartbeat, llm, storage, transaction, discovery, marketplace). Pydantic v2 models.
- **1.2** Add JSON Schema export script `scripts/export_port_schemas.py` → writes `sos/contracts/ports/schemas/*.json` on `make contracts`.
- **1.3** Add `scripts/gen_ts_types.sh` using `json-schema-to-typescript` → writes `inkwell/kernel/ports/generated/*.ts`.
- **1.4** Replace Inkwell's `types.ts` port interfaces with imports from `ports/generated/`.
- **1.5** Add import-linter contract R7: "Inkwell adapter signatures must match generated port types" (enforced via a kernel test).
- **1.6** Version bump to SOS v0.9.0, CHANGELOG, tag.

### Phase 2 — Bus stability (v0.9.1) — ✅ SHIPPED 2026-04-19

**Gate:** Zero silent drops. Every message acked. Every subject project-scoped.

**Shipped as:** tag `v0.9.1`. Delivered via the agile wave plan in
`docs/plans/2026-04-19-phase-2-bus-stability.md` (W0 → W7, one commit per
wave). See `CHANGELOG.md` for the full wave ledger.

**What landed (vs the original steps above):**

- **2.1 / W0** ✅ Both `tenant_id` and `project` are now required on
  `BusMessage` (original plan said project only; tenant_id added during
  implementation for tenant-boundary enforcement).
- **2.2 / W1** ✅ Scope stamped at the kernel boundary in `kernel.bus.send()`
  + `services/bus/enforcement.py`. Stream naming unchanged
  (`sos:stream:global:squad:*` and scoped `sos:stream:project:{tenant}:{project}:squad:*`
  coexist during transition).
- **2.3** ⚠ Deferred — Inkwell adapter migration is a separate repo; will
  land when Inkwell regenerates TS types from the new schemas.
- **2.4 / W2 + W3 + W4** ✅ Ack-or-retry implemented as XACK + background
  retry worker (exponential backoff 30s → 2m → 10m → DLQ). `BusPort.ack()`
  primitive on the port. DLQ schema + dashboard read route.
- **2.5** ⚠ Deferred — per-consumer pending-inspection is available via
  `XPENDING` today; a dedicated HTTP endpoint (`/inbox/unacked`) is a nice-to-have,
  not a gate.
- **2.6 / W5 + W6** ✅ Consumer migrations. Pilot: journeys (W5). Follow-up:
  brain + health (W6). Plan's original list (health, feedback, operations,
  saas) revised after recon: feedback/operations/saas have no bus consumers;
  execution/worker.py uses a task-queue pattern, out of scope.
- **2.7** ⚠ Deferred — `agent-comms.md` still lives; the code-enforcement
  primitives are in place, but ripping out the doc is a separate cleanup.
- **2.8 / W7** ✅ Tagged `v0.9.1`.

**Invariants enforced:** (a) publish without scope raises at kernel
boundary; (b) handler exceptions leave entries unacked; retry worker
reclaims after backoff; (c) 3 retries then DLQ with full metadata;
(d) envelope-level `_LRUSet` dedup on top of XACK (retry re-XADDs produce
new stream entries with the same `message_id`).

**Exit gate met:** 13/13 bus integration tests green against real Redis.
Per-service unit suites green.

### Phase 3 — Mesh enrollment (v0.9.2, ~4 days) — closes task #33 ✅ SHIPPED 2026-04-19

**Gate met:** Agents and squads self-register on boot. Dashboard lists them live at `/sos/mesh`.

**Design chosen: Option B — extend `/agents/cards` path** on durability/trust/security/intelligence.
AgentCard stays single source of truth (no third keyspace). Hardened bearer + project-scope
reused. Plan: `docs/plans/2026-04-19-phase-3-mesh-enrollment.md`. Seven waves, seven
revertable commits (W0 `9a16bdb1` → W7 `cd488f05`).

**Steps:**

- **3.1** ✅ `POST /mesh/enroll` in `sos/services/registry/app.py` — W1 commit `e97cf0db`.
  Accepts `{agent_id, name, role, skills[], squads[]?, heartbeat_url?, project?}`;
  server-fills `tool`/`type`/timestamps; `write_card(ttl_seconds=900)`.
- **3.2** ✅ Agents call `/mesh/enroll` in their startup sequence — W4 commit `3595fde5`.
  `AgentJoinService.join()` Step 8.5 (after bus announce, before nursery bounties) calls
  `AsyncRegistryClient.enroll_mesh()`. Failure non-blocking.
- **3.3** ✅ Squad subjects addressable at delivery time — W2 commit `81eaf021`.
  `GET /mesh/squad/{slug}` scans cards in scope and returns agents with slug in `squads[]`.
  Path param validated against slug regex.
- **3.4** ✅ Heartbeat-driven pruning — W3 commit `1989a3db`.
  `HeartbeatPruner` scans every 60s; 5m → `stale=True` with decremented TTL;
  15m → `redis.delete()`. Wired into registry startup/shutdown.
- **3.5** ✅ Dashboard mesh tab — W5 commit `8dd4bffe`. `GET /sos/mesh` (HTML, admin-gated)
  and `GET /sos/mesh/api` (JSON). Groups by squad; `unsquadded` bucket; 30s auto-refresh.
- **3.6** ✅ Contract test — W6 commit `885efc10`.
  `tests/contracts/test_mesh_enroll_in_bootloader.py` AST-scans `sos/agents/join.py` and
  asserts `AgentJoinService.join` references `enroll_mesh` or `/mesh/enroll`.
- **3.7** ✅ v0.9.2 shipped — W7 commit `cd488f05`, tag `v0.9.2`. Closes task #33.

**Exit gate met:** 505 tests pass across contracts + registry + dashboard + registry client.
Two fields added to AgentCard (`heartbeat_url`, `stale`) — both optional, both round-trip
through Redis. No import-linter flare (dashboard→registry already whitelisted).

### Phase 4 — Mumega-edge as canonical API (v0.9.3, ~1 week)

**Gate:** Every Inkwell instance points `workerUrl` at `api.mumega.com`. No per-instance Workers.

**Steps:**

- **4.1** Audit `mumega-edge/src/routes/` — catalog every route, classify as (a) proxy to SOS, (b) Inkwell-specific, (c) legacy-retire.
- **4.2** Add SOS-proxy routes: `/sos/bus/*`, `/sos/economy/*`, `/sos/registry/*`, `/sos/objectives/*`, `/sos/mesh/*`. Hono route groups with shared auth middleware.
- **4.3** Add Inkwell routes: `/inkwell/content/*`, `/inkwell/glass/*`, `/inkwell/shelf/*`.
- **4.4** Update `inkwell/inkwell.config.ts` base: `workerUrl: 'https://api.mumega.com'` as default.
- **4.5** Remove `workers/inkwell-api` from every per-instance inkwell fork — they import the shared config now.
- **4.6** Document in `docs/architecture/mumega-edge.md`: this Worker is the single ingress.
- **4.7** Ship v0.9.3.

### Phase 5 — `sos init` first-boot flow (v0.9.4, ~1 week)

**Gate:** `sos init <tenant>` in 5 minutes produces a live Inkwell instance with 1+ squad, heartbeat green, Day-1 report.

**Steps:**

- **5.1** `sos/cli/init.py` — flesh out existing scaffold. CLI takes `--slug --label --email --domain --industry --plan`.
- **5.2** Step A — POST to SOS saas `/tenants` (provisions tenant + DB + tokens).
- **5.3** Step B — `cp inkwell/instances/_template/ inkwell/instances/<slug>/`, interpolate config, `wrangler deploy` to CF Pages.
- **5.4** Step C — POST default squads to `/agents/cards` (social, content, outreach, analytics). Mint qNFTs in the economy for each hire.
- **5.5** Step D — Write `inkwell/instances/<slug>/standing_workflows.json` from a template.
- **5.6** Step E — Trigger first `pulse` run for this project. Sanity-check root + children created in objectives service.
- **5.7** Integration test: `sos init fake-tenant-test` full round-trip in CI, teardown after.
- **5.8** Ship v0.9.4.

### Phase 6 — Glass layer (v0.10.0, ~1 week)

**Gate:** Every tenant's `/dashboard` is self-writing. SQL → Inkwell static page. No LLM in the render path.

**Steps:**

- **6.1** Define `glass` port in `sos/contracts/ports/glass.py` — a tile is `{id, title, query, template, refresh_interval}`.
- **6.2** Implement `/glass/tiles/{tenant}` endpoint in SOS that runs the tile's SQL (or bus query) and returns rendered JSON payload.
- **6.3** Inkwell `kernel/adapters/glass/sos.ts` — fetches tile payloads, caches in KV.
- **6.4** Inkwell instance gets auto-generated `/dashboard` route rendering all tiles declared in `instances/<slug>/glass.json`.
- **6.5** Default tiles: Health (heartbeat green/yellow/red), Metabolism (wallet chart), Objectives (progress bars), Decisions (event log), Metrics (GA/GSC chart — empty until Phase 7 ships).
- **6.6** Ship v0.10.0.

### Phase 7 — Growth Intelligence squad + Shelf (v0.10.1, ~2 weeks)

**Gate:** A new tenant signs up and gets a brand-vector dossier + wallet + 1 course/book for sale, all within 10 minutes.

**Steps:**

- **7.1** Add GA/GSC/Ads OAuth adapters to `sos/services/integrations/oauth.py`.
- **7.2** Add BrightData + Apify adapters to `sos/services/integrations/` — connector-style pull into a snapshot table.
- **7.3** Create `sos/agents/growth-intel/` — a standing squad. Agents: trend-finder (pulls BrightData/Apify), narrative-synth (clusters into vector), dossier-writer (emits markdown).
- **7.4** Wire squad to pulse: daily-rhythm workflow `growth-intel` with bounty + capabilities.
- **7.5** Glass tile: "Brand Vector" — reads dossier from SOS memory, renders on `/dashboard/growth`.
- **7.6** Implement Shelf — `inkwell/kernel/adapters/commerce/sos.ts` posts Stripe-captured amounts to `/economy/credit`, grants access via D1 row.
- **7.7** Default Mumega-internal "Mumega Playbook" book wired as first Shelf product so we dogfood.
- **7.8** Ship v0.10.1.

### Phase 8 — Onboard the 8 (v1.0.0, ~1 week)

**Gate:** TROP, GAF, DNU, Viamar, Shabrang, Digid, ToRivers, Mumega-internal all running as Inkwell instances on SOS. All healthy on the Mesh tab. All producing Day-1 dossiers.

**Steps:**

- **8.1** Run `sos init mumega-internal` first — dogfood the flow.
- **8.2** Run `sos init trop` — migrates existing TROP seed from `therealmofpatterns/sos-seed/` into `inkwell/instances/trop/`.
- **8.3** Run `sos init gaf`, `sos init dnu` — SME-ready tenants.
- **8.4** Run `sos init viamar`, `sos init shabrang`, `sos init digid`, `sos init torivers`.
- **8.5** Smoke test: every instance answers HTTP 200 on `/dashboard`, every Mesh entry has a fresh heartbeat.
- **8.6** Tag v1.0.0. Write the launch post on Mumega's own Shelf.

---

## Squad assignments

Per the plan-skill "make your squad once, use them sprint" directive.

| Phase | Lead | Stateless helpers | Stateful helpers |
|-------|------|-------------------|------------------|
| 1 Contracts | Athena (architect) | Explore, general-purpose | sos-medic |
| 2 Bus | Kasra (executor) | Explore, code-reviewer | sos-medic |
| 3 Mesh | Kasra | Explore, contract-tester | sos-medic |
| 4 Edge | Codex | Explore (TS-focused) | — |
| 5 `sos init` | Athena + Kasra | general-purpose | sos-medic |
| 6 Glass | Codex (Inkwell side) + Kasra (SOS side) | Explore | — |
| 7 Growth + Shelf | Athena (design) → Kasra (SOS) + Codex (Inkwell) | Explore, general-purpose | sos-medic |
| 8 Onboard | Kasra | general-purpose | — |

**Specialist called in:** `wp-builder` (mumcp plugin) for any tenant that needs a WP surface in addition to Inkwell.

---

## Stability criteria — what "invisible SOS" means

After this plan lands:

1. **Zero silent drops on the bus.** Every message acked or dead-lettered. Dashboard shows DLQ depth; alert if > 10.
2. **Self-healing mesh.** Agent dies, heartbeat goes yellow within 5 min, subjects paused, agent restarted by operations service, subjects drained. No human intervention.
3. **One-command tenant onboarding.** `sos init <slug>` runs unattended in CI. Failures are caught there, not at the tenant.
4. **Version independence.** SOS ships a new minor version, Inkwell doesn't need to redeploy (the port registry absorbs changes).
5. **Dogfood test.** Hadi runs `mumega.com` as a user for 7 consecutive days without opening this repo. If he has to, the phase failed.

---

## Version track

| Version | Gate |
|---------|------|
| v0.9.0 | Phase 1 — port registry ✅ |
| v0.9.1 | Phase 2 — bus stability ✅ |
| v0.9.2 | Phase 3 — mesh enrollment |
| v0.9.3 | Phase 4 — unified edge |
| v0.9.4 | Phase 5 — `sos init` |
| v0.10.0 | Phase 6 — Glass |
| v0.10.1 | Phase 7 — Growth + Shelf |
| v1.0.0 | Phase 8 — the 8 projects live |

---

## What Hadi does during this plan

1. Approve (or amend) this plan.
2. Provide Google OAuth credentials for GA/GSC/Ads (Phase 7).
3. Confirm qNFT economics — what does hiring a squad cost in $MIND? (Phase 5.)
4. Tag and push releases (committed by Codex/Kasra, tagged by Hadi).
5. Run Mumega as a customer for 7 days after v1.0.0. Report any moment SOS leaked through.

Everything else is squad work.
