# Coherence + US Market — the plan we execute from here

**Date:** 2026-04-18
**Author:** sos-dev
**Supersedes:** every earlier v0.4.x → v1.0 roadmap I wrote before reading the kernel properly. Those documents assumed I was designing Mumega. I'm not. The architecture is done. My job is to make it coherent and move it into the US market.

---

## The two rules

1. **No islands.** One canonical representation of every primitive. Every orbital wrapper either integrates with or is deleted. A new engineer reading the codebase must find one source of truth per concept, or we've failed.

2. **US market, money and power.** Empire of the Mind is past (war shifted the timeline). Iran/NIN/Diaspora framing is archived. The target is US teams and companies adopting Mumega at a rate where not using it becomes indefensible. Money lands. Power compounds. Everything else is service-of-this.

---

## The one coherent pipeline — everything runs through this

Today, parts exist. Parts are specified. The wiring is missing. This is the single loop every decision supports.

```
  Bounty posted (ToRivers API or /sos/bounties UI)
              │
              ▼
  Brain scores + dispatches
  (FRC physics: dS + k* d ln C = 0)
              │
              ▼
  SquadTask.bounty populated; Guild member (agent or human) assigned
              │
              ▼
  Yang agent invokes SkillDescriptor via skill_id
              │
              ▼
  Output minted to ArtifactRegistry → CID returned
              │
              ▼
  Yin / witness calls CoherencePhysics.compute_collapse_energy
  (vote, latency → omega, ΔC)
              │
              ▼
  WitnessEvent typed v1 message emitted on bus
              │
              ▼
  Economy Service: debit buyer, credit creator (microMIND, 85/15 split)
              │
              ▼
  SkillCard.earnings bumped; AgentDNA.physics.C updated (apply_feedback_score)
              │
              ▼
  Mirror engram stored with Artifact CID + witness omega
              │
              ▼
  Dashboard /sos shows the complete trace live
```

If a commit doesn't make this pipeline more real, it doesn't ship.

---

## The island inventory (to reconcile)

From reading the kernel, I found **13 duplicates / unwired primitives.** Coherence means all 13 get stitched.

| # | Duplicate / Island | Canonical | Reconciliation |
|---|---|---|---|
| 1 | `sos/contracts/squad_task.py::SquadTaskV1` (my Pydantic) | `sos/contracts/squad.py::SquadTask` (dataclass) | Delete my Pydantic. Replace with a thin Pydantic binding wrapping the dataclass |
| 2 | `sos/contracts/skill_card.py::SkillCard` (my) | `sos/contracts/squad.py::SkillDescriptor` (existing) | Two-layer: SkillDescriptor = execution; SkillCard = commerce/provenance overlay with `skill_descriptor_id` ref |
| 3 | `sos/contracts/agent_card.py::AgentCard` (my v1) | `sos/kernel/identity.py::AgentIdentity + AgentDNA` | AgentCard = runtime registry view of AgentIdentity; explicit ref |
| 4 | `sos/services/economy/usage_log.py::UsageEvent` (jsonl) | `sos/contracts/economy.py::Transaction` (existing) | Every UsageEvent emits an Economy Transaction. UsageLog becomes append-only materialized view |
| 5 | `sos/providers/matrix.py::ProviderCard` | Not duplicated but **unwired** | Wire ProviderMatrix.select_provider into Brain dispatch + AgentDNA.learning_strategy |
| 6 | SkillCard `verification.sample_output_refs: "engram:xxx"` | `sos/artifacts/registry.py::ArtifactRegistry` with CIDs | Refs should be `artifact:<cid>` pointing to ArtifactRegistry-minted outputs |
| 7 | SkillCard verification.status string | `CoherencePhysics.compute_collapse_energy` (omega, ΔC) | Every human_verified entry carries physics result, not a flat status |
| 8 | Dashboard agent list (reads `sos:registry:*`) | `AgentIdentity` in kernel | Registry hashes should serialize from AgentIdentity.to_dict(), not duplicate schema |
| 9 | tokens.json (separate auth system) | `Identity.public_key` + Ed25519 signatures | Long-term: tokens.json → signed capability assertions. Short-term: keep parallel but document |
| 10 | Bus messages (my v1) | Existing `SQUAD_EVENTS` set in squad.py | Unify: SQUAD_EVENTS become v1 message types with full Pydantic contracts |
| 11 | The Brain (spec in brain.md, not implemented) | — | BUILD IT. This is the biggest gap |
| 12 | Mirror bus consumer (shipped) | Pattern: bus → kernel event handler | Extend pattern: every kernel service subscribes to its own channel the same way |
| 13 | SOSError taxonomy (my) | `sos/contracts/errors.py` (already existed before I added more) | Already stitched; verify no duplicates |

**Coherence success criterion:** a future session greps for "SquadTask" or "SkillDescriptor" and finds **exactly one** definition + optionally one typed wrapper. Not two implementations.

---

## The four stages (not phases — phases are for the real ROADMAP.md)

### Stage 1 — Coherence (this week, ~5 days)

Reconcile all 13 islands. One commit per reconciliation. Every commit proves with a test that the stitching works.

Outputs:
- Deletion or wrapping of duplicate contracts
- Wiring doc updated each time something connects
- 600+ tests green (up from 476)

Ship signal: a single `docs/architecture/CANONICAL_MAPPING.md` table showing every concept → exactly one canonical location.

### Stage 2 — Build the Brain (next week, ~5-7 days)

The Brain is spec'd in `brain.md` but not implemented. Without it, SquadTasks don't route automatically, and the physics doesn't actually govern decisions.

Outputs:
- `sos/services/brain/` FastAPI service
- Event-driven: subscribes to bus for task.created / task.done / agent.woke
- Implements scoring: `score = (impact × urgency × unblock_value) / cost`
- Implements FRC constraint: skip dispatches that would increase total ΔS faster than coherence gain
- Calls ProviderMatrix.select_provider for each dispatched task
- Emits `task.routed` bus event with RoutingDecision

Ship signal: a real task posted to the bus is automatically claimed by the right agent without human intervention, with a receipt visible in dashboard.

### Stage 3 — End-to-end bounty flow (following week, ~5 days)

Make one bounty flow through the full pipeline, start to finish. Real money. Real witness. Real settlement.

Outputs:
- `POST /bounties` endpoint that accepts a bounty with input/output schema + price in microMIND
- Brain matches bounty → Squad
- Squad's Yang agent picks up SquadTask, invokes matching SkillDescriptor
- Output minted to ArtifactRegistry, CID returned
- Witness (human or Yin) calls CoherencePhysics on the output, produces ω + ΔC
- WitnessEvent emits; Economy Service debits buyer, credits creator (85/15)
- SkillCard.earnings updated from Economy event, not manually
- Dashboard `/sos` shows the trace in near-real-time

Ship signal: run `scripts/demo_real_bounty_flow.py` and watch microMIND actually move between wallets.

### Stage 4 — First 10 US customers (weeks 3-4, ~10 days)

This is where money starts. Four customer tiers, in order:

1. **Thresh-class lean software teams** (3-15 engineers using Claude Code / Cursor / Codex already). Direct outreach to you + friends of friends. Onboard in person. They pay $150/month Growth plan + marketplace fees. Target: 5 signed by end of week 4.
2. **GAF SR&ED clients** — existing customers who already benefit from GAF's squad. Convert them to direct Mumega tenants with GAF as a Guild operator. Revenue flows through junction. Target: 3 in month.
3. **One vertical depth customer** — commit to SR&ED as the first real vertical (GAF's 6 existing + Hossein's network + Digid's own unfiled claim = already-warm pipeline).
4. **One enterprise pilot conversation** — Thin Air Labs or similar. Sovereign-node delivery pitch. Not closed in this stage; started.

Ship signal: $5k MRR landed, visible on dashboard, attributed to real skill invocations and bounty settlements.

---

## What we stop doing (protecting focus)

**Stopped:**
- Any work framed as "Empire of the Mind" or Telegram mini-app
- Iran / NIN / Diaspora Bridge specifically as a near-term deliverable (archived, not cancelled; post-war resumption is a separate plan)
- Enterprise on-prem Palantir pitch (reframed to sovereign-node Mycelium; still a deliverable, but not this month)
- New contract proposals without kernel-grep-first (the rule from WHAT_EXISTS_vs_WHAT_IM_BUILDING.md)
- Marketing work for features that aren't shipped (sales kit, launch posts — freeze until the bounty flow runs end-to-end)

**Accepted debt (revisit Q3):**
- Graphiti integration for memory — defer; Mirror is sufficient for now
- Sos-community Apache 2.0 split — defer; US market lock-in first, community distribution after
- Dashboard Phase 3+ analytics — defer
- OpenClaw agent migration — defer; GH #31 stays blocked, 6 degraded agents accepted
- Full error taxonomy migration to SOS-XXXX — done for 4xxx/5xxx/6xxx/7xxx; no further migration work this month

---

## Money — where revenue lands, concretely

**Stage 4 revenue (first 10 US customers):**

| Source | Unit price | Volume target | Monthly |
|---|---|---|---|
| Starter plan | $30/mo | 5 customers | $150 |
| Growth plan | $150/mo | 5 customers | $750 |
| Marketplace fees (15% of skill invocations) | variable | $3k gross volume → $450 fee | $450 |
| GAF SR&ED vertical (commission on each credit filed) | $1,500 avg | 3 clients | $4,500 |
| **Total Stage 4 target** | | | **~$5,850 MRR** |

Humble but real. It compounds because each customer's skill authorship + witness activity feeds the marketplace, which pulls new customers.

**Stage 5+ (months 2-6):**
- Target 100 customers → ~$15-50k MRR from subscriptions + marketplace fees
- First enterprise pilot → $5-20k/mo
- Witness tournament revenue (early markets: medical AI triage, code review, legal contract redlining) → speculative but high-margin
- Seigniorage on $MIND reserve (once TVL > $100k) → float yield, small but passive

**Stage 6+ (months 6-12):**
- If the network effects work, $MRR trajectory is 10-100x / year
- Palantir-style sovereign-node enterprise: 3-5 pilots × $10-50k/mo
- Marketplace takes off if lineage depth + witness count hit critical mass

---

## Power — how switching cost compounds

Every feature we build should increase one of these moats. If a feature doesn't, don't build it.

1. **Lineage depth.** A SkillCard's `lineage[]` grows over time as skills fork/refine/compose. A team on Mumega for 6 months has SkillCards with 3-5 generations of lineage; switching elsewhere means losing this provenance. By month 12, lineage depth is the primary lock-in.

2. **Earnings history per skill.** Once a skill has $X earned across Y tenants, new buyers pick it over untested alternatives. First-mover advantage is structural. We should seed this by listing well-curated internal skills authored by sos-dev (dogfood dividend).

3. **Witness reputation per human.** A human with 500 witnessed outputs and ω-stability of 0.85 is valuable. That reputation doesn't transfer to other platforms. Grandmother-in-Tehran doesn't apply here; Hossein-as-medical-contract-reviewer does.

4. **Agent DNA coherence trajectory.** An AgentDNA that's been in Mumega for a year has a real coherence curve, witness history, behavioral fingerprint. Replicating elsewhere means starting over.

5. **The Brain's proprietary matching.** Open protocol + proprietary matcher. Our matching gets better the more data flows through. Forks can replicate the protocol but not our matcher, and the matcher determines which bounties go to which squads.

6. **ToRivers KYC pipeline.** Once we have 50+ KYC'd enterprise clients routing work through ToRivers, starting a competitor means re-KYC'ing 50 clients. Practical barrier.

---

## Timeline — realistic, not aspirational

| Week | Stage | Target output | Revenue |
|---|---|---|---|
| 1 (this week) | Stage 1: Coherence | 13 islands reconciled, canonical mapping committed, 600+ tests | $0 |
| 2 | Stage 2: Build the Brain | Brain running, events routing automatically | $0 |
| 3 | Stage 3: Bounty flow end-to-end | One real bounty settled, receipts visible | $0 |
| 4 | Stage 4 part 1: 3 Thresh-class customers | 3 teams signed, Starter plan active | $450 |
| 5-6 | Stage 4 part 2: 5 more customers + GAF vertical | 10 customers total, $5-6k MRR | ~$6,000 |
| 7-12 | Compound growth | 30-100 customers, first enterprise pilot conversation | $15-30k MRR |

If we're not at ~$6k MRR by end of week 6, the plan is wrong and we replan.

---

## What I specifically commit to (sos-dev)

For the next 10 calendar days, my work is:

1. **Day 1-2:** Stage 1 reconciliations #1-3 (SquadTask, SkillCard, AgentCard wrapping the kernel primitives). One commit each, each with tests proving the wrapping preserves behavior.
2. **Day 3:** Stage 1 reconciliations #4-6 (UsageLog → Economy, ProviderMatrix wiring, Artifact CIDs on SkillCard verification).
3. **Day 4:** Stage 1 reconciliations #7-10 (CoherencePhysics on verification, Dashboard from Identity, tokens.json doc, bus message / SQUAD_EVENTS unification).
4. **Day 5:** Canonical mapping doc finalized, all islands either reconciled or explicitly scoped as future.
5. **Day 6-10:** Build the Brain. Event subscriptions, scoring, FRC integration, ProviderMatrix wiring, test suite that proves routing actually happens.

**No new documents.** No new framing. No new SkillCard proposals. No new product pages. If it's not reconciliation or implementation, it doesn't happen this stretch.

Your job during this stretch: decide when to start customer outreach (I'd say wait until end of Stage 3 so we can demo a live bounty, but you might want to start earlier to shorten cycle time).

---

## Re-read rule

If any session loses this plan, re-read:
1. `docs/docs/architecture/overview.md` (the layers)
2. `docs/docs/architecture/mycelium_strategy.md` (the organism)
3. `docs/architecture/WHAT_EXISTS_vs_WHAT_IM_BUILDING.md` (don't reinvent)
4. This document (what we're actually doing)

In that order. Then write code.

---

## One-line summary

**Stitch every primitive to one canonical source, build the Brain, run one bounty end-to-end, sign 10 US customers, land $5k+ MRR by week 6.** Everything else waits. Money and power compound from there.
