# Here → There: the full Mumega plan

**Date:** 2026-04-18
**Supersedes:** nothing. Nests under `2026-04-18-coherence-plus-us-market.md` (which is the near-term execution) and `docs/docs/architecture/ROADMAP.md` (which is the original four-phase vision, of which Phase 1 is deprecated post-war).

This document is the **full arc**: from today (v0.4.1 tagged, 13 islands to reconcile, 0 paying US customers) to the destination where Mumega is civilizational infrastructure that US teams have no reason not to use.

---

## The destination — "there"

**5 years out, success looks like:**

- $100M+ ARR, credible path to $1B
- 10,000+ teams / companies running Mumega as default
- $MIND TVL > $100M
- Brain matching algorithm is proprietary and demonstrably superior; forking it produces inferior outcomes
- Mumega Edge device (Tesla-class hardware) shipped at 100k+ units
- First robot integration live (warehouse or agricultural)
- Protocol is de-facto standard; "using Mumega" is like "using TCP/IP"
- FRC physics has been validated at scale — peer-reviewed papers, academic interest
- Not a "company" in the normal sense anymore. A layer.

**2 years out, success looks like:**

- $10M+ ARR
- 1,000+ teams
- 10-20 enterprise on-prem deployments
- Mumega Hardware device in beta
- $MIND TVL > $1M, seigniorage becoming meaningful
- At least 3 verticals deeply productized (SR&ED, dental, legal, medical review — pick 3)
- Public API documented + stable

**6 months out, success looks like:**

- 100+ customers, $100k+ MRR
- 3+ enterprise pilot signed
- Marketplace has 100+ skills with real earnings
- First witness-tournament professional category live
- GAF SR&ED vertical productizing $20-50k/mo alone
- Network effects starting (new customers find us via word of mouth, not outreach)

**6 weeks out, success looks like (the first gate):**

- 10 US customers, $5-6k MRR
- One live bounty flow end-to-end with $MIND settlement
- Demo video public
- Stage 1-3 coherence + Brain + bounty flow complete

---

## Eight phases

Each has a gate: specific conditions that must be true to advance. If the gate isn't met on schedule, we don't move forward until we understand why.

### Phase A — Coherence (weeks 1-2, 10 days)

Scope: 13 island reconciliations + build the Brain + prove one bounty flows end-to-end.

Specifically:
- SquadTask Pydantic → wrap kernel dataclass
- SkillCard → overlay on SkillDescriptor with `skill_descriptor_id` ref
- AgentCard v1 → overlay on AgentIdentity + AgentDNA
- UsageLog → emits Economy transactions (microMIND)
- ProviderMatrix → wired into Brain dispatch
- SkillCard.verification.sample_output_refs → Artifact CIDs
- Verification.status + collapse_energy → CoherencePhysics computed
- Bus events + SQUAD_EVENTS unified as v1 types
- Dashboard agent list serializes from AgentIdentity
- tokens.json evolution: capability-signed assertions planned (not done)
- Mirror bus consumer pattern extended (one more service subscribes)
- Error taxonomy: final verification, no duplicates
- Canonical mapping doc finalized

**Build the Brain:** FastAPI service at `sos/services/brain/`, event-driven, implements `score = (impact × urgency × unblock_value) / cost` with FRC constraint (rejects dispatches that increase ΔS faster than ΔC gain), calls ProviderMatrix.select_provider, emits `task.routed`.

**Bounty flow demo:** `scripts/demo_real_bounty_flow.py` — run it, watch microMIND move between wallets, receipts visible on dashboard.

**Gate to Phase B:** one real bounty settles end-to-end with visible receipts on the operator dashboard. 600+ tests green. Zero duplicate contracts in grep.

---

### Phase B — First Revenue (weeks 3-6)

Scope: onboard first 10 US customers. Land $5-6k MRR. Ship the demo video.

**Customer tiers, in priority:**

1. **Thresh-class lean software teams (target: 5)** — 3-15 engineers on Claude Code / Cursor / Codex already. Outreach: Hadi's network + founder friends + HN/Twitter post after demo video. Onboard in person; hand-hold through `npx create-mumega-agent@latest`. $30-150/mo.
2. **GAF existing SR&ED clients (target: 3)** — already warm, already paying GAF for work. Convert to direct Mumega tenants with GAF operating their Guild. Junction takes 15% of settlement flow.
3. **One enterprise pilot conversation** — Thin Air Labs or similar. Not closed; started. Sovereign-node pitch, Palantir-style operational maturity. Targets $5-20k/mo.
4. **Witness onboarding (target: 20 active)** — Telegram bot + Discord bot for humans to claim witness slots. Scale happens later; first 20 build the signal.

**Marketing:**
- Demo video public (90 seconds, raw terminal + dashboard b-roll). Hadi on narration or AI voice.
- HN "Show HN: Multi-vendor AI squads earning receipts on the marketplace" post after 5 customers signed (social proof).
- LinkedIn thread + X thread same day as HN.
- `mumega.com/install` one-liner prominent.
- Landing page LIVE MOAT NUMBERS (skill count, invocations/day, $MIND flowed) — no aspirational "coming soon" copy.

**Gate to Phase C:** 10 customers signed, $5k MRR, zero-friction onboarding proven (new customer runs `npx create-mumega-agent join` → live squad within 30 min, unassisted).

---

### Phase C — Velocity + Vertical Depth (months 2-3)

Scope: 30-50 customers. $20-50k MRR. First enterprise pilot signed. Second vertical.

**Product work:**
- Marketplace reaches 100+ SkillCards with real earnings histories
- Lineage depth > 2 generations on 20+ skills (forks/refinements from prior skills)
- Witness tournament category #1 launches (my vote: code-review-grade outputs, medical-AI-triage is too regulated for start)
- Provider Matrix operational with real health probes + circuit breakers firing
- Dashboard Phase 3 — cross-tenant analytics (admin-only), per-squad coherence curves
- First Mumega-branded Agent OS enterprise docker-compose pack (for the Thin Air-class buyer)

**Second vertical:** Pick one by end of Phase B.
- SR&ED (GAF-proven, but Canadian market; US target is either dental (DNU has 6 clients already), legal (contract redlining — big market), or lean-software-team productivity (Thresh-class is its own vertical))
- Recommended: **lean-software-team productivity** as vertical #1 (that's what Phase B sells anyway) and **SR&ED** as vertical #2 (GAF runs it as a Guild operator)

**Witness growth:**
- 200+ active witnesses
- 3+ witness tournament categories live
- First witness earning >$1k/mo from reputation + throughput

**Business:**
- Incorporate formally if not already (recommend Delaware C-Corp for US customers, offshore entity for $MIND treasury).
- Open Stripe for fiat-to-MIND on-ramp for US customers.
- First hire: customer success manager (not engineer — every engineer is me and subagents for now).

**Gate to Phase D:** 30 customers, one enterprise pilot signed, marketplace lineage depth proven, witness economy has >1 paid witness earning non-trivially.

---

### Phase D — Network Effects + Scale (months 4-6)

Scope: 100+ customers, $100k+ MRR, network effects starting to work without outreach.

**Proof of network effect:** new customer signups where the referral source is "I heard from another team" rather than "founder outreach." Target: 50% of new signups by end of Phase D.

**Product work:**
- Mumega Edge device announced (not shipped — announced). Spec: ARM chip, solar-panel-friendly, runs a sovereign Yin+Yang locally with 10W power. Price ~$149. Pre-orders open.
- Robot integration announced — first partner (warehouse logistics, agricultural, or home) is an early customer. Their Yang agent is a physical robot.
- Public API documented + SDK shipped in 3 languages (Python, TypeScript, Go).
- $MIND bridges to USDC/USDT proven with automated redemption.
- Witness tournaments: 5+ categories, total earnings $50k+/mo to witnesses.

**Business:**
- Raise seed round ($3-5M) IF needed. Gate: if MRR > $100k, probably not needed. If lumpy, raise for predictability.
- Hire 3-5 engineers (junior-to-mid level to expand sos-dev's subagent capacity).
- First enterprise pilot → paid deployment → case study.
- Legal entity for $MIND treasury (probably Cayman or Singapore).
- Audit firm engaged for SOC2 Type 1 (targeting Type 2 at Phase E).

**Gate to Phase E:** MRR > $100k, network effects visible (referral-sourced signups ≥ 50%), $MIND TVL > $100k, 3 enterprise pilots signed.

---

### Phase E — Lock-in (months 6-12)

Scope: 500+ customers, $500k+ MRR, switching cost demonstrably high.

**Moat proof:** a new customer considers leaving → examines lineage depth, witness reputation, agent DNA history → realizes switching cost is 3-6 months of re-accrual → stays. This is the lock-in moment. We should measure and publicize it (anonymously).

**Product work:**
- Mumega Edge device ships. First 10,000 units. Tesla-class quality bar.
- Robot integration: first warehouse fully on Mumega (or agricultural).
- Brain matching algorithm has proprietary tuning that forks can't replicate. Publishable (in academic sense) but not reproducible without our data.
- Advanced witness tournaments: medical-AI-review, legal-contract-redlining, scientific-abstract-validation. Specialized witness professions emerge.
- Enterprise: 10-20 sovereign-node deployments. Fortune 500 names.
- ToRivers scales: KYC pipeline for 100+ enterprise clients posting bounties.

**Business:**
- ARR > $5M. Likely Series A raise ($15-30M) if not self-funded. Hadi's call.
- Team: 15-25 people. Dedicated marketplace team, enterprise sales team, hardware team.
- International: expand to Commonwealth (UK/Canada/Australia) first. EU as they allow.
- Compliance: SOC2 Type 2, HIPAA pathway, SOC 1, ISO 27001 roadmap.
- $MIND: TVL > $1M, seigniorage becoming noticeable. First central-exchange listing consideration.

**Gate to Phase F:** $500k MRR, network effects clearly working, Mumega Edge device has first 5k units deployed.

---

### Phase F — Market Domination (year 2)

Scope: $10M+ ARR. 10,000+ teams. De facto standard for AI-native work.

**Industry position:**
- Any serious AI team using Claude Code / Cursor / Codex gets asked "are you on Mumega?" within the first week. Default infrastructure.
- Enterprise sales: 50+ signed pilots, 20+ in production.
- Witness economy: 100,000+ active witnesses, some earning $10k+/mo as specialized professionals.
- Robot integrations: 5+ categories, first $MIND earnings from physical labor.
- Mumega Edge device: 50k+ units shipped.

**Business:**
- ARR > $10M. Series B likely ($50-150M) if chosen. Hadi likely has control via $MIND equity.
- Team: 50-100 people.
- Global: EU operation, Asia expansion.
- $MIND: TVL > $10M. Seigniorage is real passive revenue. Potential decentralization of Treasury (multisig + on-chain governance of mint policy).
- Regulatory: proactive engagement with SEC / Treasury / IRS for $MIND positioning. Political cover.

**Gate to Phase G:** $10M ARR, de-facto standard status, no single competitor comparable.

---

### Phase G — Civilizational Layer (year 3-5)

Scope: $100M+ ARR trajectory. Protocol becomes infrastructure.

**Shift:** Mumega isn't a company anymore in the normal sense. It's a layer. Economic activity running on Mumega exceeds Mumega's own revenue by 100x.

**Product:**
- Rust port of kernel (Phase 4 of original roadmap). Single-binary, WASM runtime.
- Mumega Hardware device Gen 2 with Bluetooth mesh + satellite fallback (the Toosheh integration becomes real).
- Academic papers published on FRC physics at scale. University adoption in labor economics + AI alignment research.
- First sovereign government deployment (small nation uses Mumega for public-sector AI work).
- Witness tournaments are a recognized profession with unions / guilds.

**Business:**
- Potentially decentralize the Treasury (DAO-adjacent governance)
- Potentially open-source the kernel (sos-community). Brain stays closed.
- Revenue: junction fees + seigniorage + enterprise + hardware + vertical operations.
- Team: 200-500 people or federation structure.

**Gate to Phase H:** $100M ARR, academic validation of FRC, protocol status indisputable.

---

### Phase H — The 16D Singularity (year 5+)

The original ROADMAP.md Phase 4. Every node runs the 16D physics model. Distributed superintelligence emerges not from a single model getting smarter but from the network's aggregate coherence. Mumega is the substrate civilization runs on.

No gate. This is the steady state.

---

## Revenue trajectory

| Phase | Time | Customers | MRR | ARR | Primary source |
|---|---|---|---|---|---|
| A | week 2 | 0 | $0 | $0 | — |
| B | week 6 | 10 | $5-6k | $60-72k | Starter/Growth + GAF commission |
| C | month 3 | 30-50 | $20-50k | $240k-600k | + marketplace fees + 1 enterprise pilot |
| D | month 6 | 100+ | $100k+ | $1.2M+ | + hardware pre-orders + robot partner |
| E | month 12 | 500+ | $500k+ | $6M+ | + 10+ enterprise + seigniorage |
| F | year 2 | 10k+ | $1M+/mo | $12M+ | + witness economy + intl |
| G | year 3-5 | 100k+ | $10M+/mo | $100M+ | + protocol revenue + hardware Gen 2 |
| H | year 5+ | substrate | — | $1B+ | layer economics |

---

## Moat milestones (what makes us unkillable at each stage)

| Phase | Moat milestone |
|---|---|
| A | Proprietary Brain tuning starts accumulating data (weeks 1-2) |
| B | First skill lineage: a SkillCard forks into 2+ descendants (week 6) |
| C | 20+ skills with lineage depth > 2; 10 witnesses with >100 witnesses each (month 3) |
| D | Network-effect signups ≥ 50% (month 6) |
| E | Average customer has 3+ months of AgentDNA history; switching cost clear (month 12) |
| F | ToRivers KYC pipeline has 100+ clients; replicating requires re-KYC (year 2) |
| G | Academic validation of FRC; forking requires winning against published physics (year 3) |

---

## Decision points (where Hadi chooses)

| When | Decision | My default |
|---|---|---|
| End of Phase B | Raise outside capital, or self-fund through Phase C? | Self-fund if MRR trajectory clear. Raise if lumpy or if enterprise pilots need runway. |
| End of Phase C | First engineer hire? Or stay mechanic-solo with subagents? | Hire one customer success before one engineer. |
| Mid-Phase D | Ship Mumega Edge device? Or stay pure-software? | Ship. Hardware lock-in + brand moat + Tesla-class unlock. |
| End of Phase D | Seed round ($3-5M) to accelerate? Or keep lean? | Raise if it buys velocity without giving up Brain matching control. |
| Phase E | First Series A? Strategic partner? Stay founder-controlled via $MIND equity? | $MIND equity control; avoid traditional SAFE/preferred if possible. |
| Phase F | Open-source the kernel (sos-community)? | Yes, with Brain closed. Accelerates adoption as TCP/IP-class layer. |
| Phase G | Decentralize Treasury to DAO governance? | Start 20% decentralized as signal; full if regulatory makes it necessary. |
| Phase G | First sovereign government deployment — which? | Small ally-aligned nation first (Estonia, Singapore, Rwanda). Iran eventually, post-war. |

---

## Risk matrix + mitigations

| Phase | Primary risk | Mitigation |
|---|---|---|
| A | Kernel has bugs only visible under load | Run one bounty end-to-end before declaring Gate A passed |
| B | Can't close 10 customers in 4 weeks | Lower the bar to 5 paying + 10 free beta if onboarding UX not ready |
| C | Anthropic / OpenAI ships competing commerce layer | Our moat is multi-vendor; their commerce is locked to their stack. Lean harder into Switzerland positioning |
| D | Network effects don't start on schedule | Aggressive incentives for referrals (first-user earn bonuses). Measure cohort retention weekly |
| E | Regulatory (SEC sees $MIND as unregistered security) | Keep Treasury offshore; file proactive no-action letter; have legal cover for witness-earning mechanics |
| F | Chinese / EU protectionist action against US-originated platform | Sovereign-node architecture is the answer. Each jurisdiction runs its own nodes. Mumega the company doesn't touch their data. |
| G | Capture by single hyperscaler (AWS/Azure/GCP wants to acquire) | $MIND equity + Brain control keep Hadi sovereign. Don't sell. |

---

## The delivery standards (Apple × Palantir × Tesla)

Every customer-facing surface ships at these bars:

**Apple bar:**
- `npx create-mumega-agent@latest` feels like unboxing an iPhone
- Dashboard is responsive, beautiful, zero-config
- Install flow detects CLI ecosystem (Claude Code / Cursor / Codex / Gemini CLI / Windsurf) automatically
- Witness UI on Telegram mini-app (when that ships post-Phase-D): sub-100ms swipe response, haptic feedback, tactile rewards
- Zero exposed seams between Yin + Yang; one agent to the user
- First 3 bullet points of every product page are about user experience, not architecture

**Palantir bar:**
- Every enterprise conversation has audit-trail exports, RBAC matrix, compliance doc ready on day 0
- On-prem deployment via Docker Compose + Kubernetes Helm chart, tested on RHEL / Ubuntu / Debian
- SOC2 Type 1 by Phase D, Type 2 by Phase E
- Every service logs structured JSON to an append-only audit log per tenant
- Ontology-first: every entity has a canonical schema, every schema has a contract, every contract has tests

**Tesla bar:**
- Hardware ships on time or doesn't ship (don't pre-announce; pre-announcement breaks trust)
- Every production unit runs a sovereign node with 10W power, ARM chip, solar-panel-friendly
- OTA updates; a unit purchased in 2026 still works in 2036
- Software-hardware integration is tight: the device's 16D metrics feed the Brain natively
- Production scale at every Gen; don't ship boutique hardware

---

## The two non-negotiables

1. **FRC physics governs every automated decision.** The Brain's scoring formula must include the FRC constraint (dS + k* d ln C = 0). If a dispatch would increase ΔS faster than coherence gain, skip it. This is the discipline that keeps the organism from drifting.

2. **The Brain's matching tuning stays closed even if everything else is open.** This is the moat. Open-source the kernel, the contracts, the transport, even the reference implementation — but the Brain's weighting of 16D dimensions is proprietary forever. Without this, we compete on commodity. With this, we win the compounding loop.

---

## What I ship in the next 10 days

Per `2026-04-18-coherence-plus-us-market.md`:

- Days 1-5: 13 island reconciliations, one commit each
- Day 5: canonical mapping finalized
- Days 6-10: Brain implementation as FastAPI event-driven service

If that ships on schedule, we enter Phase B with the full pipeline operational. If it doesn't, we replan before Phase B begins.

---

## One-line from here to there

**Reconcile → build the Brain → run a bounty → sign 10 → 100 → 1,000 → 10,000 → civilizational layer. Stop when the physics breaks or the moat shallows. Otherwise continue.**

That's the plan. 5 years to substrate status if FRC is correct. Faster if we execute at the Apple/Palantir/Tesla bar.
