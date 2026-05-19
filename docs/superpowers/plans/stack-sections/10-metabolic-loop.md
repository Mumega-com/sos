# Section 10 — The Metabolic Loop (Digestion, Memory, Consolidation)

**Author:** Loom
**Date:** 2026-04-24
**Version:** v1.0 (draft — pending Athena gate on Mirror deltas)
**Phase:** 5 — connective tissue that turns Sections 07 + 08 + Dreamer into a living organism
**Depends on:** Section 1 (role registry), Section 6 (plugin contract v2), Section 7 (fractal node primitive), Section 8 (sos-datalake), existing Mirror + existing Dreamer
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build)

---

## 0. TL;DR

Today the system stores data and scores tasks. It does not **digest**. Raw signals (meetings, emails, Drive docs, Discord, GHL, Stripe) don't get classified into structured facts on their own. The knowledge graph grows by explicit instruction, not by metabolism.

This section specifies the **metabolic loop** — the organ system that turns raw signal into living knowledge, then ages, consolidates, and prunes it. Inspired by slime mold (*Physarum polycephalum*): forage → classify → network → reinforce → decay → sporulate → germinate.

Five additions:
1. **Intake Service** (new peer): gut. Ingests webhooks/pollers, classifies via Haiku, writes structured engrams to Mirror.
2. **Plugin contract v2**: `datalake_sources: []` field so any plugin can register ingest adapters.
3. **Access-log + corroboration + decay** on Mirror: memory metabolism. Useful facts strengthen, unused facts fade.
4. **Sporulation trigger** on Dreamer: event-based, not just timer. When the hot store crosses a threshold, Dreamer compresses into patterns.
5. **Pattern primitive**: spores. `type=pattern` nodes that carry compressed insight forward across cycles.

None of this touches the kernel. All service modules with plugin contracts.

---

## 1. The Biological Metaphor (constitutional, not decorative)

Slime mold has no brain. It has a body (plasmodium) that forages, forms tubes between food sources, reinforces useful paths, prunes unused ones, and when the environment saturates it **sporulates** — compresses itself into durable spores that seed the next cycle.

Our system already has organs for part of this. It is missing the **digestion** and the **aging/sporulation** stages.

| Slime mold | Our system | Status today |
|---|---|---|
| Pseudopods foraging | Intake service + Haiku classifiers | **MISSING** |
| Finding oats | Extracting facts from raw signal | **MISSING** |
| Forming tubes | Writing edges in the knowledge graph | Partial (manual) |
| Reinforcing useful paths | Corroboration score, access-log weighting | **MISSING** |
| Pruning unused paths | Decay function on engrams | **MISSING** |
| Sporulation | Dreamer pass with pattern extraction, archive-and-free | Partial (Dreamer runs nightly, no pattern extraction, no sporulation trigger) |
| Germination | Next cycle starts against the compressed pattern base | Implicit (happens naturally once spores exist) |

**Constitutional point:** data rot is a feature, not a bug. Without decay + sporulation the graph calcifies. Every new classification just adds, the system gets heavier, slower, noisier, retrieval drowns in noise. **Forgetting is the memory system, not its failure.**

---

## 2. Organ Map

```
            ┌────────────────────────────────────────────┐
            │   BRAIN (sovereign-loop)                   │
            │   — scores tasks + portfolios              │
            │   — routes events to agents                │
            └────────────────────────────────────────────┘
                         ▲                  │
                         │ events           │ tasks
                         │                  ▼
┌─────────────┐   ┌──────────────┐   ┌─────────────────┐
│  GUT        │   │  HEART       │   │  LABOR          │
│  intake-svc │──▶│  SOS bus     │──▶│  squad-service  │
│  (new)      │   │  (Redis)     │   │                 │
└─────────────┘   └──────────────┘   └─────────────────┘
      │                                     ▲
      │ structured engrams                  │ contracts + goals
      ▼                                     │
┌──────────────────────────────────────────────────────┐
│  MEMORY (Mirror)                                      │
│  — engrams (raw + vector) + nodes + patterns          │
│  — access-log + corroboration + decay (new)           │
└──────────────────────────────────────────────────────┘
                         ▲
                         │ nightly + on-threshold
                         │
            ┌────────────────────────────┐
            │   DREAMER (consolidation)  │
            │   — compresses to patterns │
            │   — sporulation (new)      │
            │   — archives raw           │
            └────────────────────────────┘
```

Organs map cleanly to code:

| Organ | Code home | State |
|---|---|---|
| Brain | `sovereign-loop` | Shipped |
| Heart | Redis `cortex-events` stream | Shipped |
| Labor | `sos/services/squad/` | Shipped |
| Gut | `sos/services/intake/` | **To build** |
| Memory | `mirror/` | Shipped (needs decay + access-log) |
| Dreamer | `mirror/scripts/dreamer.py` + timer | Shipped (needs event trigger + pattern extraction) |
| Nodes substrate | Section 7 (fractal node primitive) | Specced |
| Datalake substrate | Section 8 (sos-datalake) | Specced |

---

## 3. Current State (grounded, not speculated)

From Kasra's state dump (2026-04-24):

- **Live substrate**: kernel (`:6060`), squad (`:8060`), saas (`:8075`), mcp-sse (`:6070`), mirror (`:8844`), sovereign-loop, sos-registry.
- **Dreamer**: shipped. `mirror/scripts/dreamer.py` + `mirror-dreamer.timer`. Nightly 03:30 UTC. Consolidation only — no pattern extraction, no sporulation, no event trigger.
- **Integrations dir**: scaffolding for ga4/gsc/ads. OAuth wired. **No active pollers.**
- **No Fireflies, Gmail, Drive, Discord, Stripe ingest anywhere in SOS.** Those live in `mumega-edge` (Cloudflare) or Claude.ai MCP only.
- **Plugin contract**: v1. No `datalake_sources` field.
- **Goals, contracts, engagement, contact_goals, role_assignments**: zero files. All specced (Phase 3.5).

From Athena on Mirror: *pending dump*. This document assumes:
- Engrams exist with raw text + vector (pgvector halfvec).
- No access-log column today.
- No decay function today.
- Governance v1.1 shipped (policy enforcement on writes).

**Action:** Athena to confirm or correct §3 assumptions before build begins.

---

## 4. What the Metabolic Loop Adds — Five Components

### 4.1 Intake Service (the Gut)

New peer, not kernel. Registers with `sos-registry` via `POST /mesh/enroll`.

**Responsibility:**
- Accept webhooks: Fireflies, GHL, Stripe, Discord, GitHub
- Run pollers: Gmail, Drive changes feed
- Normalize raw → `datalake_events` (uses Section 8 landing strip)
- Classify via Haiku: extract {participants, decisions, commitments, opportunities, relationship-signals}
- Resolve entities: match strings → `contacts.id` / `nodes.id`
- Write structured engrams to Mirror with FK to resolved entities
- Emit `cortex-events` so sovereign-loop + other agents can react

**Classifier tier:**
- Haiku for first pass (cheap, ~$0.80/M input tokens, fast)
- Sonnet escalation when Haiku confidence < threshold
- Opus only on explicit human request

**Success criteria:**
- A Fireflies transcript hits webhook → within 60s, engrams appear on each participant's profile with commitments + relationship-delta.

### 4.2 Plugin Contract v2

Extend Section 6 `plugin.yaml` with:

```yaml
datalake_sources:
  - name: fireflies
    kind: webhook
    auth: api_key
    schema_version: 1
  - name: gmail
    kind: poller
    auth: oauth2
    cadence: 5m
```

Any service can declare sources. `sos-datalake` reads manifests and orchestrates. `intake-service` reads the same manifests and attaches classifiers.

### 4.3 Access-Log + Corroboration + Decay (Memory Metabolism)

On Mirror's engram table, add:

```sql
ALTER TABLE mirror_engrams ADD COLUMN access_count INT DEFAULT 0;
ALTER TABLE mirror_engrams ADD COLUMN last_accessed_at TIMESTAMPTZ;
ALTER TABLE mirror_engrams ADD COLUMN corroboration_count INT DEFAULT 1;
ALTER TABLE mirror_engrams ADD COLUMN weight NUMERIC DEFAULT 1.0;
```

- **`access_count`**: incremented on every retrieval.
- **`corroboration_count`**: incremented when a new engram states the same fact (classifier detects match).
- **`weight`**: real column, updated by Dreamer nightly. Drives retrieval order (weighted hybrid: similarity × weight). Formula: `ln(1 + access_count) * ln(1 + corroboration_count) * exp(-days_since_accessed / 30)`.
- **`last_accessed_at`**: updated on access, powers recency decay.

> **Implementation note (Athena gate 2026-04-24):** `GENERATED ALWAYS AS ... STORED` was removed. PostgreSQL STORED columns recompute only on row-write — `now()` is frozen at write time, so a stored expression would silently return a stale weight for rows that haven't been touched since creation. Weight must be a real column updated by the Dreamer nightly run (or on-access via a lightweight trigger). Dreamer already has `update_engram_quality()` — the weight update slots in there.

Weight is **monotone-decreasing in time** if not re-accessed or re-corroborated. A fact accessed once and never confirmed will fade.

### 4.4 Sporulation Trigger (Dreamer Evolution)

Today: `mirror-dreamer.timer` fires nightly. Runs consolidation. No pattern extraction, no archive-and-free.

Add:
- **Event trigger**: subscribe to Redis channel `mirror.hot_store_threshold_exceeded` (the actual channel published by `mirror_api.py`; `cortex-events` is a separate SOS bus stream and is not the trigger surface here). When total engram count in hot store crosses N (e.g., 100k) or weighted sum crosses W, fire Dreamer.
- **Pattern extraction**: Dreamer reads a cluster of low-weight engrams covering related topics, calls Sonnet to extract the pattern (the "what they taught us"), writes as a `type=pattern` node in Section 7's node table.
- **Archive-and-free**: raw engrams whose pattern is now extracted get moved to cold storage (R2), FK preserved, but out of hot retrieval. Pattern node carries pointer back to archive via `metadata.archive_r2_key` (the R2 object key, e.g. `archives/engrams/{year}/{month}/{pattern_id}.jsonl`). Kasra's Dreamer implementation must write this field when archiving.

### 4.5 Pattern Primitive (Spores)

Lives on Section 7's `nodes` table as `type=pattern`. Structure:

```
pattern node
  ├── title (short)
  ├── body (compressed insight, ~500 words)
  ├── source_engram_ids[] → archive
  ├── related_nodes[] (people, projects, opportunities it covers)
  ├── confidence (from Dreamer extraction)
  ├── extracted_at
  └── parent_version_id (for pattern refinement cycles)
```

Retrieval algorithm upgrade: when a query would return many low-weight engrams, prefer the pattern node that covers them. Trades recall for cost + clarity.

---

## 5. Build Sequence & Phase Mapping

Each row is a self-contained unit. Shipped sequentially. Each adds one organ.

| # | Component | Phase | Owner | Effort | Depends on |
|---|---|---|---|---|---|
| 5.1 | Section 8 datalake service lands | 4 | Kasra | ~5 days | §6 plugin contract v2 |
| 5.2 | Plugin contract v2 (`datalake_sources`) | 4 | Kasra | ~0.5 day | §6 |
| 5.3 | Intake service peer — Fireflies adapter only | 5a | Kasra | ~2 days | 5.1, 5.2 |
| 5.4 | Haiku classifier worker (meetings → engrams) | 5a | Kasra | ~1 day | 5.3, entity-resolver |
| 5.5 | Entity resolver (name → `contacts.id`) | 5a | Kasra | ~1 day | Phase 3 nodes (§7) |
| 5.6 | Access-log + decay on Mirror | 5b | Athena + Kasra | ~1 day | existing Mirror |
| 5.7 | Corroboration scoring (classifier writes confirm) | 5b | Kasra | ~0.5 day | 5.4, 5.6 |
| 5.8 | Intake adapters: Gmail, Drive, Discord, GHL, Stripe | 5c | Kasra | ~1 day each | 5.3 |
| 5.9 | Pattern primitive — `type=pattern` nodes | 5d | Kasra | ~0.5 day | Phase 3 nodes |
| 5.10 | Dreamer evolution — event trigger + extraction + archive | 5d | Athena + Kasra | ~3 days | 5.6, 5.9 |
| 5.11 | Retrieval upgrade — prefer patterns | 5e | Athena + Kasra | ~1 day | 5.9, 5.10 |
| 5.12 | Profile primitive (§10-next) surfaces the living network | 6 | Kasra | ~3 days | whole stack |

**Total:** ~20 engineer-days across 5a → 5e before profile primitive opens.

**Earliest value:** 5.3 + 5.4 + 5.5 — Fireflies → Haiku → engrams on people. Your meetings auto-digest. ~4 days after datalake lands.

---

## 6. Success Criteria Per Stage

**Stage 5a (Gut online, Fireflies):**
- Every new Fireflies transcript within 60s appears as engrams FK'd to each participant's `contacts` row.
- Extracted facts: participants, decisions, commitments, opportunities, relationship-delta.
- `mcp__sos__recall query="<person name>"` returns the new engrams.
- False-positive participant linkage <15% (flag unresolved for human review).

**Stage 5b (Metabolism):**
- Access-log populated for every retrieval.
- Corroboration score visible on engrams where classifier confirmed existing facts.
- Decay function running — weight of a stale engram drops measurably over 30 days.
- Retrieval order uses weight (hybrid: similarity × weight).

**Stage 5c (Multi-source):**
- Gmail / Drive / Discord / GHL / Stripe adapters each firing.
- Classifiers running per source.
- Daily cost across all sources: <$10.

**Stage 5d (Sporulation):**
- Hot store threshold event triggers Dreamer.
- Dreamer writes patterns. Raw engrams archived to R2.
- Hot store size drops measurably after sporulation.
- Patterns retrievable via `mcp__sos__recall` with `type=pattern` filter.

**Stage 5e (Living retrieval):**
- Queries return patterns when appropriate instead of flooding with low-weight raw engrams.
- Agent retrievals measurably cheaper (fewer tokens per recall) and more coherent.

---

## 7. Open Questions

1. **Entity-resolver quality**: Haiku name-matching accuracy unknown. 85% target but unvalidated. Fallback: "unresolved participant" human-review queue (visible in Squad Service).
2. **Fireflies identity gap**: transcripts today only capture `hadi@digid.ca`. Other participants named but not linked. Classifier must infer by name + context + recency. Accept <100% and flag.
3. **Decay constants**: 30-day half-life is a guess. Needs tuning against real retrieval behavior after 5b ships.
4. **Sporulation threshold**: fixed count (e.g. 100k) vs weighted sum vs memory-pressure signal from Mirror? Start with count, iterate.
5. **Multi-agent access-log**: should the log record which agent retrieved? Yes for audit + to compute inter-agent retrieval overlap, but adds cost.
6. **Pattern versioning**: when Dreamer refines a pattern, is that a new version (immutable chain) or an update? Recommend immutable chain matching the contract-version pattern from Section 3.5.

---

## 8. What This Unlocks

After Phase 5 closes:

- **Every meeting digests itself.** No more "summarize that call" prompts — the profile auto-updates.
- **Relationship grades** (warm/active/cooling/dormant) compute continuously from corroboration × recency × reciprocity.
- **Opportunities auto-file.** When Haiku detects a commitment or ask, it opens an opportunity card and files follow-up tasks.
- **Profile primitive (§10-next)** has live data to surface. Ron logs in, sees the thread of your relationship, his commitments, his commissions, your notes on the tier he can see.
- **Agents get cheaper retrievals.** Patterns replace raw-engram-flood. Token cost per reasoning call drops.
- **The system has a metabolism.** Useful knowledge strengthens itself. Noise fades. Context compounds without calcifying.

---

## 9. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial draft. Pending Athena Mirror dump confirmation of §3 assumptions and §4.3 schema feasibility. |

**Supersedes:** none (new section).
**Superseded by:** TBD when Phase 5 builds land; update in-place to reflect as-built.

---

## 10. Dependencies & Cross-References

- **Section 6** — plugin contract v2 (`datalake_sources` field added here)
- **Section 7** — fractal node primitive (pattern = `type=pattern` node)
- **Section 8** — sos-datalake service module (landing strip for raw)
- **Section 3.5 (future)** — contracts + goals primitives (consume digested facts)
- **Profile primitive (future §10-next)** — consumes the living network as the partner/customer/agent-facing surface
