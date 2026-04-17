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

**Stage 1 progress (live status):**

| # | Duplicate / Island | Canonical | Status | Commit |
|---|---|---|---|---|
| 1 | `sos/contracts/squad_task.py::SquadTaskV1` (my Pydantic) | `sos/contracts/squad.py::SquadTask` (dataclass) | ✅ Done — wraps dataclass with from/to converters | `9925e90e` |
| 2 | `sos/contracts/skill_card.py::SkillCard` (my) | `sos/contracts/squad.py::SkillDescriptor` (existing) | ✅ Done — SkillCard now carries `skill_descriptor_id`; input/output schemas demoted to echo | `28fc8dff` |
| 3 | `sos/contracts/agent_card.py::AgentCard` (my v1) | `sos/kernel/identity.py::AgentIdentity + AgentDNA` | ✅ Done — AgentCard now carries `identity_id`; type enum expanded (+ hermes / codex / cma / human) | `2f7cb81d` |
| 4 | `sos/services/economy/usage_log.py::UsageEvent` (jsonl) | `sos/contracts/economy.py::Transaction` (existing) | ⏳ In flight — subagent running | — |
| 5 | `sos/providers/matrix.py::ProviderCard` | Not duplicated but **unwired** | ⏸ Deferred to Stage 2 — wires into Brain when Brain lands | — |
| 6 | SkillCard `verification.sample_output_refs: "engram:xxx"` | `sos/artifacts/registry.py::ArtifactRegistry` with CIDs | ✅ Done — pattern accepts `artifact:<cid>` (canonical) or `engram:<slug>` (legacy backward-compat) | `ee3f8fec` |
| 7 | SkillCard verification.status string | `CoherencePhysics.compute_collapse_energy` (omega, ΔC) | ✅ Done — `VerificationInfo.witness_events[]` carries physics per event; `record_witness()` is the canonical write | `505a8ce1` |
| 8 | Dashboard agent list (reads `sos:registry:*`) | `AgentIdentity` in kernel | ✅ Done — new `sos/services/registry/` reads through typed AgentIdentity | `12714270` |
| 9 | tokens.json (separate auth system) | `Identity.public_key` + Ed25519 signatures | ✅ Plan doc shipped at `docs/architecture/TOKENS_EVOLUTION.md` — 3-phase migration (dual-write → verify-alongside → capability-first). No code in this phase. | `03cc3da3` |
| 10 | Bus messages (my v1) | Existing `SQUAD_EVENTS` set in squad.py | ✅ Done — v1 types renamed to dot-separated (`task.created`), + 3 new types (`task.routed`, `task.failed`, `skill.executed`) | `419c0fa8` |
| 11 | The Brain (spec in brain.md, not implemented) | — | ⏸ Deferred to Stage 2 — Build this next. Biggest single-island gap. | — |
| 12 | Mirror bus consumer (shipped) | Pattern: bus → kernel event handler | ✅ Pattern canonicalized at `docs/architecture/MIRROR_BUS_CONSUMER_PATTERN.md` (5 invariants + when-to-use-it guidance) | `03cc3da3` |
| 13 | SOSError taxonomy (my) | `sos/contracts/errors.py` (already existed before I added more) | ✅ Audit complete — 24 codes across 4xxx/5xxx/6xxx/7xxx bands, no duplicates | inline |

**New item surfaced 2026-04-18 (post-Hermes question):**

| # | Gap | Canonical answer | Status |
|---|---|---|---|
| 14 | Agent bootstrap — running agent without credentials can't connect to bus (Hermes case) | `scripts/sos-agent-bootstrap.sh` + `/sos/pairing` dashboard route | Slotted into v0.4.3 alongside Brain build |

**Coherence success criterion:** a future session greps for "SquadTask" or "SkillDescriptor" and finds **exactly one** definition + optionally one typed wrapper. Not two implementations. **Currently met for 10 of 13 islands** (island #4 in flight, #5 deferred to Stage 2, #11 is Stage 2 itself).

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

## What I specifically commit to (sos-dev) — live status

**Day 1 (2026-04-18): 10 of 13 islands reconciled in one day** via parallel subagent dispatches. Faster than the original "5 days" estimate.

- ✅ Islands #1-3 — SquadTask, SkillCard, AgentCard overlay kernel primitives (commits `9925e90e`, `28fc8dff`, `2f7cb81d`)
- ✅ Islands #6-8 — Artifact CIDs, CoherencePhysics witness, Dashboard registry (commits `ee3f8fec`, `505a8ce1`, `12714270`)
- ✅ Islands #9-10, #12-13 — Tokens plan, bus events unified, bus-consumer pattern, error taxonomy audit (commits `419c0fa8`, `03cc3da3`)
- ⏳ Island #4 — UsageLog → Economy transactions (subagent in flight)
- ⏸ Island #5 — ProviderMatrix wired to Brain (waits for Stage 2)
- ⏸ Island #11 — Build the Brain (Stage 2 itself)
- ⏸ Island #14 (new — surfaced by the Hermes question) — Agent bootstrap (`sos-agent-bootstrap.sh` + `/sos/pairing`) slots into Stage 2

**Next steps:**

1. **When island #4 lands (within the hour):** run full test suite, update `CANONICAL_MAPPING.md` as the single coherence reference, tag **`v0.4.2 "Coherent Foundation"`**, CHANGELOG entry.

2. **Stage 2 starts (~5-7 days):** Build the Brain at `sos/services/brain/`. FastAPI, event-driven, scoring + FRC constraint (dS + k*d ln C = 0) + ProviderMatrix wiring (closes island #5). Plus the **agent bootstrap** work — `scripts/sos-agent-bootstrap.sh` + `/sos/pairing` dashboard route — closes island #14 and solves the "Hermes is running but can't find its token" problem at the protocol level, not one-off per agent.

3. **End of Stage 2:** tag **`v0.4.3 "The Brain + Bootstrap"`**.

4. **Stage 3 (~5 days):** run one real bounty through the full pipeline. Witness emits physics, economy settles 85/15, SkillCard earnings auto-bump, dashboard trace visible. Tag **`v0.4.4 "First Bounty"`**.

5. **Phase B starts:** 10 US customers. Target $5-6k MRR by week 6.

**Discipline:** every commit advances the pipeline or closes an island. No new orbital primitives, no new framing, no new product pages until Stage 3 green.

Your job during Stage 2: the Hermes token reload is cleaner once `sos-agent-bootstrap.sh` lands (2-3 days). Park Hermes until then. Customer outreach decision: my recommendation is wait until end of Stage 3 so you demo a live bounty, not an aspirational one — unless you want to stretch the cycle and have contracts ready to sign the day the bounty flows.

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
