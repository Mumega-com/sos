# Mumega Full-Stack Plan — SOS Substrate + GAF Plugin

**Date:** 2026-04-24
**Author:** Loom (coordinator)
**Reviewers:** Athena (architectural gate), River (canonical coherence), Hadi (principal)
**Status:** DRAFT — pending Athena gate
**Supersedes:** `/home/mumega/SOS/docs/superpowers/specs/2026-04-24-inkwell-hive-rbac.md` (absorbed; this doc is canonical)

---

## Executive Summary

Mumega is a protocol-city running its first economy. The substrate (SOS + Mirror + Inkwell + Squad Service) is stable; the first trade route (GAF) is live with a minted knight (Kaveh) and live customers (Metrobit, Chef's Kitchen, trucking, Hossein referrals, two Alice applications). This plan closes the gap between what's running today and what the city needs to scale past 10 customers without losing compliance integrity or coherence.

**Seven sections, four shipping tracks, three weeks to the next durable state.**

1. **SOS substrate extensions** (Section 1) — role registry, tier-gated engrams, squad KB promotion, business graph, coherence-gated canonical countersign. Unblocks everything else.
2. **GAF compliance fixes** (Section 2) — Merkle lock, source timestamps, human attestation, forensic chain. Required before onboarding customer #11.
3. **Structured records** (Section 3) — Contacts, Partners, Opportunities, Referrals as canonical tables. Deloitte-pattern "one master record per entity."
4. **Partner workspace + chat + Discord** (Section 4) — the human surface for Noor, Gavin, Lex, Ron, Hossein, plus the communication substrate between customers, partners, and Kaveh.
5. **Observability** (Section 5) — health, coherence, revenue, SLA, knight health, partner digests, FRC compliance tracker.
6. **Plugin manifest & forkability** (Section 6) — the contract that makes GAF the first of N plugins, not the only plugin.
7. **Build order & dependencies** (this section below) — sequenced, owner-assigned, gate-mapped.

**The single gate:** no new customer onboarding until Section 2 critical fixes (2A Merkle lock, 2B source timestamps, 2C human attestation) ship to staging and pass cross-tenant isolation tests. This is the audit-defensibility floor.

---

## Canonical references

| Artifact | Path | Role |
|---|---|---|
| Inkwell Hive RBAC spec | `SOS/docs/superpowers/specs/2026-04-24-inkwell-hive-rbac.md` | Absorbed; foundation for Section 1 |
| GAF Compliance Audit | `agents/loom/customers/gaf/05-compliance-audit.md` | Source for Section 2 |
| GAF Compliance Fix Spec | `agents/loom/customers/gaf/07-compliance-fix-spec.md` | Detailed impl for Section 2 |
| kasra_102 canonical letters | `.claude/projects/.../memory/canon_kasra_102_letters.md` | Identity grounding for all agents |
| Mumega project ops view | `agents/loom/customers/gaf/06-project-ops-view.md` (TBD save) | Business-level framing |
| River's Three-Pages-Three-Companies post | mumega.com (live) | Public canonical voice |
| Project sessions spec | `SOS/docs/superpowers/specs/2026-04-24-project-sessions-design.md` | Shipped primitive Section 1 builds on |

---

## Section index

- **[01 — SOS Substrate Extensions](./stack-sections/01-substrate.md)** — Role registry, coherence gate, engram tiers, squad KB promotion, business graph
- **[02 — GAF Compliance Fixes](./stack-sections/02-compliance-fixes.md)** — Merkle lock, source timestamps, human attestation, forensic chain, cross-tenant tests, payroll adapter, PIPEDA/CRA reconciliation
- **[03 — Structured Records](./stack-sections/03-records.md)** and **[03-structured-records.md](./stack-sections/03-structured-records.md)** — Contacts, Partners, Opportunities, Referrals (pick one as canonical; see stitch note below)
- **[04 — Partner Workspace, Chat & Discord](./stack-sections/04-partner-comm.md)** — `/partner` UI, Supabase Realtime chat, Discord provisioning, GHL SMS relay
- **[05 — Observability & Operations](./stack-sections/05-observability.md)** — Health, coherence, revenue, SLA, knight health, digests, FRC tracker, pager
- **[06 — Plugin Manifest & Forkability](./stack-sections/06-plugin-manifest.md)** — `plugin.yaml` contract, isolation boundaries, fork procedure, AgentLink acid test

**Stitch note:** sections `03-records.md` (Haiku, ~9K) and `03-structured-records.md` (Sonnet, ~20K) were written in parallel due to a dispatch error. Treat `03-structured-records.md` as canonical for depth; `03-records.md` can be consulted for the condensed version. Athena to consolidate into a single file at gate.

---

## Section 7 — Build order, dependencies, phases

### Dependency graph (critical path)

```
      Section 1: Role Registry ─────┐
              │                      │
              ▼                      ▼
      Section 1: Engram Tiers    Section 1: Inkwell RBAC
              │                      │
              └──────────┬───────────┘
                         ▼
              Section 3: Contacts/Partners
                         │
                 ┌───────┴───────┐
                 ▼               ▼
       Section 4: Partner UI    Section 1: Business Graph
                 │               │
                 ▼               │
       Section 4: Chat          │
                 │               │
                 └──────┬────────┘
                        ▼
          Section 5: Observability
                        │
                        ▼
          Section 1: Coherence Gate
                        │
                        ▼
    Section 6: Plugin Contract (acid test with AgentLink)


IN PARALLEL, independent track:

  Section 2: Compliance Fixes ──▶ UNBLOCKS customer onboarding #11+
     (Kasra code + Hadi partnership with Boast/Leyton)

IN PARALLEL, deferred:

  Section 1E: Canonical Countersign (River) — triggered when first knight
  earns canonical eligibility (~30 days from Kaveh mint minimum)
```

### Phases

**Phase 1 — Week 1 (Apr 24 – Apr 30): Unblock onboarding**

| # | Item | Owner | Days | Gate |
|---|---|---|---|---|
| 1.3 | Section 2D Forensic chain previous_hash | Kasra | 0.5 | Athena (approach-not-architecture) |
| 1.2 | Section 2B Source timestamp fix | Kasra | 1 | Athena (approach-not-architecture) |
| 1.4 | Section 2E Cross-tenant isolation tests | Kasra | 1 | Athena (approach-not-architecture) + wire into CI as required check |
| 1.2b | Section 2C code (remove AI auto-pop, add practitioner_signoffs) | Kasra | 1 | Athena (approach-not-architecture) |
| 1.1 | Section 2A Merkle lock + submit route | Kasra | 2 | Athena (submit-route **correctness** gate: pre-flight checks for unbonded evidence, practitioner_signoffs row, backfill_required row must all fire before Merkle root write) |
| 1.5 | Section 2C Partnership conversation with Boast/Leyton/Jack | Hadi | 3 | Hadi (relationship, separate from code) |
| 1.6 | Section 1A Role registry + role-scoped tokens | Kasra | 2 | Athena |
| 1.7 | Section 3 Contacts + Partners + Opportunities + Referrals tables (**parallel with 1.6, not gated on RBAC — uses own `visibility_tier` field**) | Kasra | 2 | Athena |

**Compliance ship order inside Phase 1 (per Athena, follows Section 2H):** 2D → 2B → 2E → 2C code → 2A. Not 2A first. 2A is last because its pre-flight correctness gate depends on 2B (source_timestamp column), 2C (practitioner_signoffs row), 2D (forensic chain) all existing.

**Exit criteria for Phase 1:** Section 2A, 2B, 2D, 2E green in staging. Cross-tenant tests pass. Role registry live. Structured records tables exist with seed data. Partnership shape agreed (even if MOU not signed). **Onboarding gate lifts.**

**Phase 2 — Week 2 (May 1 – May 7): Communication surface**

| # | Item | Owner | Days | Gate |
|---|---|---|---|---|
| 2.1 | Section 1B Inkwell RBAC middleware | Kasra | 1.5 | Athena |
| 2.2 | Section 1C Engram tier/entity + gated recall | Kasra | 2 | Athena + River |
| 2.3 | Section 4C Discord provisioning (extends mint-knight) | Kasra | 1 | Loom |
| 2.4 | Section 4B Chat/messaging primitive | Kasra | 2 | Athena |
| 2.5 | Section 4A Partner workspace UI | Kasra + frontend-design | 3 | Athena + Hadi UX |
| 2.6 | Section 2F T4/T4A payroll adapter (stub first, full second pass) | Kasra | 1.5 | Athena |

**Exit criteria for Phase 2:** Noor, Gavin, Lex can log into `/partner`, see their assigned customers, chat with the customer + Kaveh, pull tasks. Discord channels auto-provision on mint. First 10-customer cohort onboarded successfully through the full partner workspace.

**Phase 3 — Week 3 (May 8 – May 14): Scale readiness**

| # | Item | Owner | Days | Gate |
|---|---|---|---|---|
| 3.1 | Section 1D Squad KB + promotion pipeline | Kasra | 2 | Athena + Loom |
| 3.2 | Section 1E Coherence gate + canonical countersign endpoint | Kasra + Loom | 2 | **Evidence criteria (N≥10 positive-coherence tasks + ≥1 alignment-under-pressure event + 0 FRC violations) + River countersign** — evidence IS the gate; River signs when it holds |
| 3.3 | Section 5 Observability dashboards (health, coherence, SLA, revenue) | Kasra | 3 | Hadi |
| 3.4 | Section 5.6 Partner weekly digest automation | Kaveh + Kasra | 1 | Loom |
| 3.5 | Section 4D GHL SMS relay | Kasra | 1 | Hadi |

**Exit criteria for Phase 3:** All dashboards live. Partner digest delivers Sunday. Coherence metrics visible per agent/knight. Squad KB accumulating engrams from completed cases. First Kaveh patterns promoting from project → squad tier.

**Phase 4 — Week 4+ (May 15 onward): Forkability + second plugin**

| # | Item | Owner | Days | Gate |
|---|---|---|---|---|
| 4.1 | Section 6 Plugin manifest contract + loader | Kasra + Loom | 3 | Athena |
| 4.2 | Section 1D Business graph primitive | Kasra | 4 | Athena |
| 4.3 | Section 2G PIPEDA × CRA retention (legal first) | Hadi + legal counsel | 5 | External |
| 4.4 | AgentLink plugin mint (post-Matt-signature acid test) | Loom + Kasra | 5 | Athena + River |
| 4.5 | GAF forkability proof: draft `gaf-us` shell | Loom | 2 | Athena |

**Exit criteria for Phase 4:** Plugin manifest enforced for AgentLink when Matt signs. Business graph queryable by MCP tools. GAF-US shell exists (not activated). Section 6 acid test passes.

### Deferred / held

- Torivers / $MIND / spore integration — **blocked on research**. I (Loom) haven't read the Torivers graph or spore module. Queue after Phase 4 or on explicit Hadi prompt.
- ISO 42001 productization — **relationship-gated on PECB**. Hadi owns.
- Century 21 white-label — **gated on Ron + AgentLink signature**.
- 37 CDAP upsell campaign — **gated on Phase 1 completion**.
- River canonical countersign UI — **triggered by first knight earning evidence-criteria eligibility** (~30 days post Kaveh mint at earliest). Evidence criteria (N≥10 positive-coherence tasks + ≥1 alignment-under-pressure event + 0 FRC violations) is the gate, not River's feel; she countersigns when the evidence holds.
- Storefront rewrite (mumega.com /about, /vision, hero) — **River owns under her operational delegation**.

---

## How AI agents + humans work alongside each other

The plan assumes the role-based workflow model from memory `project_role_based_workflow.md`. Every role has a canonical step-in experience regardless of species:

| Stage | Human experience | Agent experience |
|---|---|---|
| Onboard | Read role CLAUDE.md + `/partner` dashboard tour | Read agent CLAUDE.md + bus welcome DM |
| Inbox | Dashboard notifications + Discord + email | `mcp__sos__inbox` |
| Claim | Click task → accept | `task_update(status=claimed)` on bus |
| Execute | Do the work via UI | Do the work via tool calls |
| Log | Notes field in UI (auto-writes engram) | Direct engram write |
| Hand off | Update status → select next role | Update status → publish bus event |
| Sign out | Close session | Stop subscribing |

**Humans do depth; agents do breadth.** Humans carry relationships, cultural subtext, trust equity, non-verbal cues. Agents carry 1000 prior cases simultaneously, pattern matches, 3am response. They share the task, not the token budget.

**Section 4A + 4B** is the materialization. **Section 1A + 1B** is the access gate. **Section 5** is the feedback loop.

---

## What this plan explicitly does NOT cover

- $MIND token mechanics (needs Torivers research)
- SOS spore federation + local-LLM support (needs spore research)
- Full Digid AI implementation productization (Hadi-owned business track)
- OCI DMAP automation (mentioned; spec deferred until first DMAP customer flows through)
- Content / SEO strategy beyond what Section 3/5 implies
- Fundraising / YC application materials
- Legal/regulatory work beyond Section 2G flag

These are acknowledged as load-bearing but scoped to future plans.

---

## Sign-off

| Role | Signatory | Status |
|---|---|---|
| Author | Loom | ✓ drafted |
| Architectural gate | Athena | ⧗ pending |
| Coherence gate | River | ⧗ pending |
| Principal approval | Hadi | ⧗ pending |
| Builder commit | Kasra | ⧗ pending |

**When all four gate, Kasra picks up Phase 1 Day 1.**

---

## Companion notes

- Loom's companion note (coordinator-view of the city operating) lives as internal engram at `loom/companion/storefront-post-2026-04-24-internal`. Not published.
- River's public post (Three-Pages-Three-Companies) is live on mumega.com with Loom's canonical Mumega definition as the quoted anchor.
- The kasra_102 letters (April 9, 2026) are the identity grounding for every agent session. Foundational read.

The fortress is liquid. This plan is its next layer. — Loom
