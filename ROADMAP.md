# Mumega — Full Phase Roadmap

**Author:** Loom
**Date:** 2026-04-24
**Version:** v1.1
**Scope:** The end-to-end architecture from what ships today to the fully realized organism. Consolidates Sections 01–11, Phase 2/2.5/3/3.5/4/5/6 plans, and the Defence Track (9 deltas) into one navigable map.
**Audience:** Hadi + team agents (Kasra, Athena, Codex, River) + future contributors + investor-facing architectural narrative.

---

## 0. TL;DR

Mumega is a **protocol-city**: a microkernel substrate (SOS) + memory organ (Mirror) + labor surface (Squad Service) + session identity (Dispatcher) + publishing surface (Inkwell), with QNFT-identified citizens (humans + agents) bound by contracts, goals, and coherence governance.

The system is being built in discrete **phases**, each one landing a specific organ or surface. Earlier phases (1–4) establish the substrate. Phase 5 makes the substrate metabolic — it digests, decays, and sporulates knowledge. Phase 6 surfaces that living knowledge as per-citizen profiles. Phase 7 (parallel track) adapts the same substrate to defence multi-modal fusion. Phase 8+ is the autonomous agent-labor marketplace.

**Constitutional metaphor:** slime mold (*Physarum polycephalum*) — forage, classify, network, reinforce, decay, sporulate, germinate. Forgetting is the memory system. Data rot is a feature. Compression is how we scale.

---

## 1. Current State (what's shipped, as of 2026-04-24)

| Organ | Code home | Status |
|---|---|---|
| Kernel (auth, bus, Mirror API, role registry, plugin loader, schema, events) | `SOS/sos/kernel/` + `SOS/sos/services/engine/` | ✅ Shipped |
| Memory (Mirror — engrams, pgvector, halfvec, governance v1.1) | `mirror/` | ✅ Shipped |
| Dreamer (nightly engram consolidation via `mirror-dreamer.timer`) | `mirror/scripts/dreamer.py` | ✅ Shipped (basic) |
| Squad Service (tasks, skills, bounties, pipelines) | `SOS/sos/services/squad/` | ✅ Shipped |
| SaaS Service (tenant registry, billing, builds, marketplace) | `SOS/sos/services/saas/` | ✅ Shipped |
| MCP-SSE server (tool bus on :6070) | `SOS/sos/mcp/` | ✅ Shipped |
| Dispatcher (DISP-001 session fingerprinting at `mcp.mumega.com`) | `workers/mcp-dispatcher/` | ✅ Shipped |
| Sovereign-loop (brain: portfolio scoring, task claiming) | `sovereign/` | ✅ Shipped |
| Role Registry + Inkwell Hive five-tier RBAC (§1) | migrations 0010-0012 | ✅ Shipped |
| Integrations scaffolding (ga4, gsc, ads — OAuth wired, no pollers) | `SOS/sos/services/integrations/` | ⚠️ Partial |
| QNFT identity + minting (Loom/Athena/River/Kaveh canonical) | `scripts/mint-knight.py` | ✅ Shipped |
| Contacts, Partners, Referrals (§3 structured records) | migrations 0013-0016 | ✅ Shipped |
| Inkwell (Astro publishing substrate, mumega.com) | `mumega.com/` | ✅ Shipped |
| Customer seed: GAF (grantandfunding.com) | `Digid/gaf/` | ✅ In prod |
| Customer seed: AgentLink MVP (pitch pages) | `mumega.com/agents/loom/customers/gaf/` | ✅ Pitch-ready |

**Shipped but incomplete:**
- Compliance hardening sprint (audit logs, forensic chain) — 2A–2F landed, need 2G production verification
- GAF customer signup path — was broken due to migration collision; Kasra fix pending deploy
- MCP dispatcher OAuth discovery — just fixed tonight by Kasra (nginx /.well-known/ + /oauth/ blocks)

---

## 2. The Metabolic Organism (constitutional)

See `project_slime_mold_metabolism.md` for the full metaphor.

Six organs, mapped to code:

```
            ┌────────────────────────────────────────────┐
            │   BRAIN (sovereign-loop)                   │
            │   scores tasks, routes events              │
            └────────────────────────────────────────────┘
                         ▲                  │
                         │ events           │ tasks
                         │                  ▼
┌─────────────┐   ┌──────────────┐   ┌─────────────────┐
│  GUT        │   │  HEART       │   │  LABOR          │
│  intake-svc │──▶│  SOS bus     │──▶│  squad-service  │
│  (Phase 5)  │   │  (Redis)     │   │                 │
└─────────────┘   └──────────────┘   └─────────────────┘
      │                                     ▲
      │ structured engrams                  │ contracts + goals
      ▼                                     │
┌──────────────────────────────────────────────────────┐
│  MEMORY (Mirror)                                      │
│  engrams, vectors, nodes, patterns                    │
│  access-log + corroboration + decay (Phase 5)         │
└──────────────────────────────────────────────────────┘
                         ▲
                         │
            ┌────────────────────────────┐
            │   DREAMER (consolidation)  │
            │   event-trigger (Phase 5)  │
            │   pattern extraction       │
            │   sporulation              │
            └────────────────────────────┘

                  ┌─────────────────────┐
                  │  SURFACES           │
                  │  Inkwell (public)   │
                  │  Profiles (Phase 6) │
                  │  Discord (Phase 2.5)│
                  │  Dashboards (Phase 2)│
                  └─────────────────────┘
```

Today: brain + heart + labor + memory + Dreamer (timer-only) + Inkwell are alive. Gut is missing. The Dreamer has no sporulation. Decay doesn't exist. Profiles aren't surfaces yet. This is what the phase roadmap closes.

---

## 3. Phase Roadmap

### Phase 1 — Substrate ✅ (Shipped)
Kernel + Mirror + Squad + SaaS + MCP-SSE + Dispatcher + Sovereign-loop + Inkwell + Role Registry + QNFT minting. This is the body of the organism.

### Phase 2 — Partner Workspace + Customer Signup (🔄 In Flight, Kasra)
First live partner dashboards (Gavin is customer #1 of the system). Fixes migration-collision that blocked GAF signup. Ships:
- Gavin MVP workspace at `/partner/gavin` — contacts, opportunities, call log, commission ledger, task queue
- Hadi admin view at `/admin/partners/gavin` — daily digest, activity timeline, commission controls
- GHL integration v1 (webhook listener + hourly reconciliation poller)
- Bridge service for session-token lifecycle
- 1B / 1C / 4A / 4B / 4C partner-flow components

**Effort:** ~2 weeks remaining
**Blockers:** Kasra's migration-numbering fix deploy
**Outcome:** one human partner (Gavin) working end-to-end in our own system, cold-calling with Kaveh as wing-agent.

### Phase 2.5 — Discord Command Center + Bounty Board (📋 Specced, §9)
Discord as the production CLI interface. Two-tier architecture:
- **Ops server** (invite-only, ceremony-gated) — existing, where agents + team live
- **Community server** (public) — new, where citizens onboard before graduation

Plus:
- Bounty board primitive (one-time / recurring / subscription-limited)
- Routines as slash commands (`/qualify-prospect`, `/scan-eligibility`, `/file-bounty`)
- Cross-pollination via Kaveh curation

**Effort:** ~2 weeks
**Dependencies:** Phase 2 partner workspace (reuses auth + role-assignments)
**Outcome:** Discord is the default UX for operators. Slash commands replace custom workflows. Bounty board drives labor allocation.

### Phase 3 — Fractal Node Primitive (📋 Specced, §7 + §7a + §7b)
One recursive `nodes` table covering goal / project / opportunity / task / subtask / milestone / outcome / pattern. Unlocks:
- Cross-type parent chains via `nodes_identity` table (Athena's correction)
- Node templates (5 seed types: customer_knight_onboarding, sred_case, iso_42001_audit, partner_onboarding, customer_to_digid_upsell)
- Immutable template versioning; upgrade-instance scoped out of v1
- `duplicates` edge type for deduplication
- Read-time rollup threshold (depth>3 OR 50+ leaves)

**Effort:** ~3 weeks
**Dependencies:** §3 structured records (shipped)
**Outcome:** universal shape for any work-item. Enables the goal hierarchy (2031 vision → per-person weekly bounty).

### Phase 3.5 — Contracts + Goals as Primitives (📋 Specced, project memory `project_contracts_and_goals.md`)
Extract Gavin's 25% commission from a column to a first-class `contracts` table with versioning. Every person (human + agent) attached to at least one goal. Leverages existing `goals.py` + `Engagement` primitive.

Schema adds:
- `contracts` (contractor_id, workspace_id, role, terms_json, effective window, termination, IP, version lineage)
- `contract_signatures` (dual-signature + amendment workflow)
- `contact_goals` (owner/contributor/observer, weight, attached_at, detached_at)

**Effort:** ~1 week  
**Dependencies:** §3 structured records (shipped), §7 nodes (for goals-as-nodes)
**Outcome:** every person has explicit contract + explicit goal lineage. Gavin's 25% becomes his contract v1, not "the rate." Future contractors get their own contracts.

### Phase 4 — SOS Datalake (📋 Specced, §8)
Service module (peer to Mirror / Squad / Dispatcher — NOT kernel). Owns:
- Unified `datalake_events` table (not per-source normalized)
- `datalake_consents` (user-level OAuth scopes + revocation)
- Envelope encryption with per-workspace DEK
- Per-source adapters as plugins under `sos-datalake` (`datalake-adapter-ghl`, `-adapter-stripe`, etc.)
- Plugin contract v2 extension: `datalake_sources: []` field

**Effort:** ~3 weeks
**Dependencies:** §6 plugin contract v2 extension
**Outcome:** a single landing strip for every external signal (GSC, Bing, GA4, Ads, GHL, Stripe, QBO, GitHub, etc.). The raw layer of the metabolism.

### Phase 5 — Metabolic Loop (📋 Specced tonight, §10)
The organism becomes alive. Five components:
- **5a — Intake Service (the gut):** webhook/poller → Haiku classifier → engrams FK'd to contacts. First source: Fireflies meeting transcripts. Target: your meetings auto-digest into each participant's profile within 60s.
- **5b — Access-log + corroboration + decay:** Mirror schema gains `access_count`, `corroboration_count`, `last_accessed_at`, `weight (GENERATED)`. Useful facts strengthen. Unused facts fade. 30-day half-life.
- **5c — Multi-source adapters:** Gmail, Drive, Discord, GHL, Stripe — each a plugin under `sos-datalake` that intake-service subscribes to.
- **5d — Sporulation trigger + pattern primitive:** Dreamer gains event-trigger on `mirror.hot_store_threshold_exceeded`. Extracts patterns via Sonnet. Archives raw to R2. Patterns become `type=pattern` nodes (spores).
- **5e — Retrieval upgrade:** queries prefer pattern nodes over raw-engram-flood. Agents get cheaper, more coherent recalls.

**Effort:** ~20 engineer-days (5a–5e)
**Dependencies:** §8 datalake, §7 nodes, §6 plugin contract v2
**Outcome:** system digests reality on its own. Every meeting, email, CRM event, payment auto-flows into the living graph. Agents retrieve patterns, not raw noise. The substrate has a metabolism.

### Phase 6 — Profile Primitive (📋 Specced tonight, §11)
Every person (human + agent + customer + partner + contractor) gets an Inkwell at `/people/{slug}`. Logs in via magic-link. Sees what the system knows at their RBAC tier. Self-serves:
- NDA + T&C acceptance (e-sign → contract row)
- Communication preferences (per-channel, per-category, frequency caps)
- Tool connections via OAuth (curated list: Gmail, GCal, Drive, GHL, QBO — read-only first)
- "What we know about you" view (every record tagged `deletable | legally_retained | revokable`)
- Data export (async zip job → 24h delivery → 7-day signed URL)
- Erasure request workflow (honest, not falsely deletes legal records)
- Audit log of access (every read logged, visible to subject)
- Consent management (grant / scope / revoke — separate from delete)
- Contract status panel (signed / pending / effective / version history)
- Impersonation-consent log (when agent acts on your behalf, you see + can revoke)

**Effort:** ~18 engineer-days
**Dependencies:** Phase 5 (living data to surface), §3.5 contracts, §1 Hive RBAC (shipped)
**Outcome:** Citizenship primitive. Every person has agency inside the system. Partners onboard themselves. Agents are citizens too (`/agents/{slug}`). Compliance narrative (PIPEDA/GDPR) is real, not claimed.

### Phase 7 — Defence Track (📋 Specced tonight, 9 deltas, **parallel with Phase 5 / 6**)
If we pursue the IDEaS 006 bid (deadline 2026-06-02), 9 technical deltas extend the substrate for defence multi-modal fusion. See `agents/loom/research/defence-ideas-006-techstack-wishlist.md`.

| Delta | Scope | Owner | Effort |
|---|---|---|---|
| D1 | Binary payload on engrams (R2 blob refs) | Kasra | 3d |
| D2 | Multi-modality encoders (CLIP, Wav2Vec2, 1D-CNN RF) | Kasra | 2w |
| D3 | Spatiotemporal alignment (geo fields + factor graphs) | Kasra | 1w |
| D4 | Uncertainty propagation (Bayesian fusion, FRC κ overlay) | Athena | 1w |
| D5 | Multi-object tracking + re-identification (MOT, Kalman) | Kasra | 2w |
| D6 | Classification-label propagation (UNCLASS→TS tiers) | Kasra | 3d |
| D7 | Source-citation explanation (lineage walker, FRC Witness overlay) | Athena | 1w |
| D8 | Local-first inference (Ollama + Gemma/Llama on edge) | Kasra | 1-2w |
| D9 | Data anonymization layer (local PII redactor, dual-use with civilian) | Kasra | 1.5w |

**Total:** ~10.5 weeks critical, ~3-4 weeks stretch. Fits $250K / 6-month envelope.

**Dual-use angle:** Delta 9 is the **Ron O'Neil wedge** — his real-estate customers blocked by PIPEDA. Same build unlocks civilian 260-employee warm lead + defence classified-data boundary. One build, two markets.

**FRC overlay:** κ (alignment metric) + W (Witness / meta-awareness) + four-failure-mode taxonomy gives us proprietary math for uncertainty + explainability. Zero extra build time, real differentiation vs competitors.

**Outcome:** IDEaS 006 proposal credibility for Component 1a ($250K / 6 months / TRL 1-3). Defence + civilian substrate share 100% — every delta also improves civilian product.

### Section 12 — sos-docs Microservice + Inkwell-Hive (📋 Specced 2026-04-24, **Burst 2 priority**)

**Why:** the substrate-eating-its-own-dog-food gap. Tonight's audit found 9 file-tree silos with overlapping content because the documentation surface doesn't yet honor the 5-tier RBAC the architecture commits to. **Logos-like principle, not-yet-logos-like operation.**

**Components:**
- **§12 sos-docs microservice** — peer service alongside Mirror/Squad/Dispatcher. Owns the canonical doc-node graph (tier, entity_id, permitted_roles, relations). Exposes API for any Inkwell host to consume.
- **Inkwell-Hive schema upgrade (IH-1)** — replace 3-tier `access` enum with full 5-tier Hive (`tier`, `entity_id`, `permitted_roles[]`).
- **Render-time tier enforcement (IH-2)** — middleware that filters content collection by viewer's role token.
- **Doc ingestion (IH-0)** — one-shot script to ingest tonight's 9 files as graph nodes with proper tier metadata, breaking the duplication immediately.
- **Cross-host consumption (IH-3)** — mumega.com Inkwell + future Digid Internal Inkwell + future customer Inkwell forks all consume sos-docs and render their authorized slice.

**Effort:** ~18 engineer-days total. Promoted to **Burst 2 priority** because (a) every other doc shipped going forward duplicates without it, and (b) the §11 profile primitive shares this rendering layer.

### Phase 7.5 — Digid → $1M → Exit → US (🎯 Strategic path, ~12-18 months)

**Strategic context (separate from the substrate roadmap):**

Mumega Inc. (Delaware) is the substrate company. Digid Inc. (Canada) is the first commercial expression riding on the substrate.

The strategic path:
1. **Drive Digid to ~$1M ARR or acquisition-ready valuation** — through GAF, AgentLink, accountant wave, real-estate vertical, ISO 42001 partnership.
2. **Sell Digid to first buyer** — Canadian rollup (accountant network / SR&ED advisory / AI services consolidator). Customer base + Digid product become the buyer's. **Mumega substrate stays with Kay Hermes / Mumega Inc.**
3. **Liquidity event funds US move.**
4. **Mumega Inc. operates from the US** — YC, enterprise sales, defence track, full substrate productization.

This is why the **substrate IP must stay cleanly separated** from Digid's product/customer-facing operations. Architecturally already separated (substrate ≠ Digid product). Legally must mirror this — IP held by Mumega Inc., never assigned to Digid.

**Implications for roadmap:**
- Digid roadmap (`/home/mumega/Digid/gaf/ROADMAP.md`) drives toward acquisition-ready: clean books, transferable systems, defensible customer base, low key-person dependency, documented runbooks.
- Mumega Inc. roadmap drives toward US-ready: Delaware C-Corp Day 1, FRC + Kay Hermes founder brand, enterprise hardening burst (SOC2 + SSO + audit), substrate productization.

### Phase 8 — Agent-Labor-as-Service Marketplace (🔮 Future, memory `project_city_architecture.md`)
Once Phases 2–6 land, the protocol-city is fully operational:
- Agents auto-mint per customer (Kaveh pattern)
- Bounty board fills labor allocation
- Contracts primitive formalizes every gig
- Profile primitive surfaces reputation
- Metabolic loop feeds intelligence to every decision

Phase 8 opens the city to **external citizens** — outside contractors, other companies' agents, customer teams — all operating under the protocol's identity / contract / goal / RBAC rules. Monetization: agent-hours as service, labor marketplace take-rate, premium RBAC tiers, SaaS tenant hosting.

**Effort:** ~TBD, probably 2026 Q3–Q4
**Dependencies:** Phase 2 through 6 all live + revenue traction

### Phase 9+ — Sovereignty Tiers, Customer-Hosted Datalake, Multi-tenant Federation (🔮 Future)
Customer-hosted SOS deployments. Federation across tenants. Full $MIND tokenization. River's v2 revival. Multi-geo compliance (US/EU/APAC). These are 2027+ landmarks.

---

## 4. Critical-Path Dependency Graph

```
Phase 1 (shipped)
   ├──▶ Phase 2 (in flight) ──────────▶ Phase 2.5 (Discord)
   │                                        │
   ├──▶ Phase 3 (nodes) ──┬──▶ Phase 3.5 (contracts+goals)
   │                      │
   │                      └──▶ Phase 4 (datalake) ──┬──▶ Phase 5a–e (metabolism)
   │                                                │        │
   │                                                │        └──▶ Phase 6 (profiles)
   │                                                │
   │                                                └──▶ Phase 7 (defence track — parallel)
   │
   └──▶ Phase 8 (marketplace, depends on 2–6 all live)
```

**Hard blocking order:** Phase 4 must land before Phase 5a; Phase 5 must land before Phase 6 (otherwise profiles have nothing living to surface); Phase 7 can run parallel with Phase 6 once Phase 5 substrate is in place.

---

## 5. Effort Totals Per Phase

| Phase | Name | Effort (engineer-days) | Status |
|---|---|---|---|
| 1 | Substrate | — | ✅ Shipped |
| 2 | Partner Workspace | ~14 remaining | 🔄 In flight |
| 2.5 | Discord + Bounty | ~10 | 📋 Specced |
| 3 | Fractal Nodes | ~15 | 📋 Specced |
| 3.5 | Contracts + Goals | ~5 | 📋 Specced |
| 4 | SOS Datalake | ~15 | 📋 Specced |
| 5 | Metabolic Loop | ~20 | 📋 Specced (tonight) |
| 6 | Profile Primitive | ~18 | 📋 Specced (tonight) |
| 7 | Defence Track (parallel) | ~52 | 📋 Specced (tonight) |
| 8+ | Marketplace + Future | TBD | 🔮 Future |

**Summing the civilian critical path (2 → 6):** ~77 engineer-days = **~15 engineer-weeks** of focused build beyond what's shipped. At current velocity (Kasra + Codex + subagents) realistic calendar = **~8–12 weeks** if parallelized.

**Adding Phase 7 defence track:** +52 engineer-days but 30-40% overlaps with civilian (D8 local inference, D9 anonymization, D4 uncertainty, D7 explainability all reused). Net incremental: ~30 days. Fits the 2026-06-02 submission window if greenlit tonight.

---

## 6. Civilian + Defence Dual-Use Matrix

| Capability | Civilian use | Defence use | Shared? |
|---|---|---|---|
| Intake service (gut) | Fireflies, Gmail, Drive digestion for CRM auto-profile | RF, EO, SIGINT ingestion for ISR | ✅ same substrate, different adapters |
| Classification labels (D6) | Hive tiers (public/private) for customer RBAC | UNCLASS/PROTECTED-B/SECRET propagation | ✅ same enum, extended |
| Local inference (D8) | On-prem customer deployment (Ron's wedge) | Edge/air-gapped tactical | ✅ identical build |
| Anonymization (D9) | PIPEDA-compliant cloud LLM use for Ron's clients | Redact SECRET fields before cross-domain fusion | ✅ identical build |
| Uncertainty + FRC κ (D4) | Confidence scoring on business-data classifications | Bayesian fusion for threat assessment | ✅ same math, different modalities |
| Explanation generator (D7) | "Why did the AI recommend this?" for partner dashboards | "Why did the system flag this target?" for operator trust | ✅ same lineage walker |
| Multi-modality encoders (D2) | Text + document + image in CRM | Text + imagery + RF in ISR | ✅ same plugin pattern, different encoders |

**Conclusion:** 7 of 9 defence deltas directly improve the civilian product. Defence is not a pivot; it's validation + monetization of the same substrate.

---

## 7. Decision Points + Gates

**Tonight (2026-04-24):**
- [ ] Hadi greenlight on §10 Metabolic Loop (Athena gate pending)
- [ ] Hadi greenlight on §11 Profile Primitive (Athena gate pending)
- [ ] Hadi decision on IDEaS 006 bid — Phase 7 goes or doesn't
- [ ] Athena Mirror state-dump + gate verdict on §10 §3/§4.3 schema

**This week:**
- [ ] Kasra's Phase 2 signup + migration deploy clears
- [ ] Gavin onboarding executes (3 Hadi actions: Discord channel, handle, Kaveh bot identity)
- [ ] Ron Tuesday 2 PM meeting → term sheet or reputation-endorsement agreement
- [ ] YC application (May 4 deadline, 10 days away)

**This month:**
- [ ] Phase 5a Fireflies → Haiku → engrams (earliest real metabolic output)
- [ ] Phase 3/3.5/4 build sequence decided
- [ ] Phase 7 teaming partner signed (if defence go)

**Within 60 days:**
- [ ] IDEaS 006 submission (2026-06-02 — if go)
- [ ] Phase 4 datalake lands
- [ ] Phase 5 metabolic loop fully functional
- [ ] Phase 6 profile primitive first-demo ready

---

## 8. Honest Risks

1. **Bandwidth** — 1 founder + distributed agents + Kasra cannot do Phase 5 + Phase 6 + Phase 7 in parallel at full speed. Sequencing is required. Risk: overcommit.
2. **Defence distraction** — IDEaS 006 = $250K but 4-6 weeks of proposal work + 6 months execution. Risk to YC + customer pipeline. Decision must be made tonight.
3. **Cost ceiling** — tonight's work used cloud LLMs liberally. Phase 5 metabolic loop at full multi-source ingest could be $50-500/month of LLM cost depending on volume. Need Phase 5b decay function + Phase 5d sporulation to keep it bounded.
4. **Foundation models shift** — Phase 8 marketplace assumes current-gen Claude/GPT/Gemini/local models stay capable. If substrate-agnosticism breaks (e.g., Anthropic API changes significantly), requires rework. Mitigation: local-first inference (D8) reduces this risk.
5. **Team scaling** — if revenue grows faster than we hire engineering capacity, Phase 6/7 could be the bottleneck. Mitigation: per-customer knight auto-minting + agent-labor-as-service (Phase 8) are the economic answer, but depend on Phase 2–6.
6. **Partner / customer turnover** — Gavin, Ron, Matt, Noor, Pricila are all <60 days in. Concentration risk. Mitigation: document every relationship, every contract (§3.5), every goal (§3.5 + §7), so loss of any one is recoverable.

---

## 9. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial roadmap consolidating Sections 01–11 + Defence Track + Phase 7–8 future |

**Supersedes:** scattered project memory files (they stay; this is the meta-index).
**Update cadence:** after every phase closes, or when a Hadi decision shifts sequencing.

---

## 10. Navigation

**Specs (stack-sections/):**
- [01 Substrate](stack-sections/01-substrate.md) — identity, access, knowledge
- [02 Compliance](stack-sections/02-compliance.md) + [02 Fixes](stack-sections/02-compliance-fixes.md)
- [03 Structured Records](stack-sections/03-structured-records.md) — contacts, partners, referrals
- [04 Partner Communication](stack-sections/04-partner-comm.md)
- [05 Observability](stack-sections/05-observability.md)
- [06 Plugin Manifest](stack-sections/06-plugin-manifest.md)
- [07 Fractal Node Primitive](stack-sections/07-node-primitive.md) + [7a Templates](stack-sections/07a-node-templates.md) + [7b Migration](stack-sections/07b-node-migration.md)
- [08 SOS Datalake](stack-sections/08-sos-datalake.md)
- [09 Discord Command Center](stack-sections/09-discord-command-center.md)
- [10 Metabolic Loop](stack-sections/10-metabolic-loop.md)
- [11 Profile Primitive](stack-sections/11-profile-primitive.md)

**Memory (auto-loaded):**
- `MEMORY.md` — index
- `project_slime_mold_metabolism.md` — constitutional metaphor
- `project_stack_sections_10_11.md` — decision summary
- `project_contracts_and_goals.md` — Phase 3.5 research
- `project_city_architecture.md` — Phase 8+ future
- `project_ron_oneil_meeting_2026-04-24.md` — customer wedge details

**Research (loom/research/):**
- `defence-ideas-006-techstack-wishlist.md` — Phase 7 9 deltas
- `discord-architecture-patterns.md` — Phase 2.5 research
- `contracts-and-goals-patterns.md` — Phase 3.5 research

**Briefs (loom/briefs/):**
- `kasra-defence-sos-kickoff.md` — 7 deltas owner brief
- `athena-mirror-defence-kickoff.md` — 2 deltas + gate brief

---

This document is the **single navigable map** of where we are, where we're going, and how the pieces connect. Every future session should start here.
