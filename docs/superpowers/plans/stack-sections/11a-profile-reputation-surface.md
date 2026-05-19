# Section 11a — Profile Reputation Surface (extension of §11)

**Author:** Loom
**Date:** 2026-04-25
**Version:** v0.1 (draft on `loom` branch — companion to §11 v1.0)
**Phase:** 6 (carries with §11 profile primitive)
**Depends on:** Section 11 (profile primitive — same surface), Section 10a (reputation metabolism), Section 15 (reputation Glicko-2), Section 16 (matchmaking lambda_dna 16D)
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build)

---

## 0. TL;DR

§11 lists what the profile shows: meetings, engrams, contracts, consents,
audit log. §11a adds **the citizen's standing in the substrate** — their
current reputation score (with confidence interval), their lambda_dna
position summary, and the trend lines for both.

This closes §11 Q6 (raised in the Phase 6 kickoff brief): when §10a
reputation metabolism ships, surface it on the profile so citizens see
where they stand.

---

## 1. Why surface reputation

§11 §1 principle 1: *transparency by default*. A citizen should be able
to see what the system knows about them. Reputation is among the most
load-bearing things the system knows — it determines which quests they
get matched to (§16 Stage 1 + Stage 2 + Stage 4). Hiding it from the
subject violates the transparency principle.

§11 §1 principle 5: *every access is logged*. Logging access to a value
that the subject can't see is asymmetric. Surfacing reputation at
self-tier balances the audit relationship.

The pushback case: visible reputation could be gamed (citizen sees
their score, optimizes behaviour to inflate it). Three mitigations:
- §10a inertia means rapid burst-optimization is suppressed at the
  scoring layer.
- The visible value is the *current Glicko-2 μ with σ confidence
  interval* — not a single score the citizen can chase. They see
  "1450 ± 120 over the past 30 days" — directional, not gameable.
- FRC veto (§16 Stage 2) is independent of reputation. Gaming
  reputation does not bypass coherence checks.

---

## 2. Three additions to §11

### 2.1 Standing panel on self-tier profile (new §11.11)

Render at `/people/{slug}` self-tier:

```
Standing
─────────────────────────────────────────
Reputation:       1450 ± 120  (μ ± σ, 30-day)
                  ▁▂▃▄▅▆▇▆▅▄▃  (sparkline, weekly buckets)

Active capabilities (top-5 from lambda_dna projection):
  • code-review        (last reinforced: 2 days ago)
  • spec-drafting      (last reinforced: 5 days ago)
  • contract-negotiation (last reinforced: 12 days ago)
  • migration-design   (last reinforced: 18 days ago)
  • adversarial-audit  (last reinforced: 31 days ago, fading)

Recent matchmaking outcomes (last 10 quests):
  ✓ ✓ ✓ ✗ ✓ ✓ ✓ ✓ ✗ ✓     (8/10 success rate)
```

Three subsections:
- **Reputation panel:** current μ, σ as confidence interval, 30-day
  sparkline of weekly μ values.
- **Active capabilities:** top-5 lambda_dna projection cardinal
  directions, with `last_reinforced_at` per (from §10a). Capabilities
  with stale reinforcement render with reduced opacity (visual analog
  of high `flow_inertia` from §10a).
- **Recent matchmaking outcomes:** last 10 quest outcomes (from
  match_history). Pure win/loss visualization. Aggregate as 8/10
  success rate. Hover for quest details.

### 2.2 Reputation history view (new §11.12)

A separate page at `/people/{slug}/standing/history` (self-tier only)
showing:
- Full reputation event log (`reputation_events` table) with
  `quest_id`, `outcome`, `delta_mu`, `delta_phi`, `accepted_or_skipped`
  (the §10a inertia gate's decision).
- Filter: by date range, by quest tier, by outcome.
- The `accepted_or_skipped` column is significant: it shows the citizen
  *what evidence the system did not accept*, which is the §10a inertia
  visibility surface.

### 2.3 Lambda_dna position visualization (new §11.13)

`/people/{slug}/standing/position` (self-tier only):

A 16D vector cannot be drawn directly. Show three projections:
- **Top-K capability cardinal directions** with weights (already in
  §11a.2.1 panel; this view shows the full ranked list, not just
  top-5).
- **Cosine-similarity to canonical clusters** (e.g., "you are 0.78
  similar to citizens working on infrastructure-track quests, 0.31
  similar to content-track"). Canonical clusters are seeded by Loom +
  Athena per the §14 inventory taxonomy.
- **Movement over time:** a 2D PCA projection of the citizen's
  lambda_dna at weekly snapshots, drawn as a path. Reveals "where
  they're flowing" in capability space.

This is the FRC-Resonator-Block visible to the citizen — they see
their phase-locked attractors.

---

## 3. RBAC tier extensions

Additions to §11 §4 RBAC mapping:

| Section | Public | Squad | Project | Role | Entity | Private (self) |
|---|---|---|---|---|---|---|
| Reputation panel (§11a.2.1) | — | — | — | — | summary | full |
| Reputation history (§11a.2.2) | — | — | — | — | — | full |
| Lambda_dna projections (§11a.2.3) | — | — | — | — | — | full |

`summary` for entity-tier viewers (Hadi viewing partner profiles) =
current μ ± σ + sparkline only. No reputation event log, no
lambda_dna projections — those stay self-only.

Reasoning: a partner's reputation summary is legitimately operationally
useful for staffing decisions (entity-tier RBAC is for the people who
make those decisions). The full history is theirs alone.

---

## 4. Build sequence

| # | Component | Owner | Effort | Gate |
|---|---|---|---|---|
| 11a.1 | Standing panel SSR component (self-tier profile) | Kasra | 0.5d | G82 |
| 11a.2 | Reputation history page + filters | Kasra | 0.5d | G83 |
| 11a.3 | Lambda_dna projection page + canonical-cluster seed data | Kasra | 1d | G84 |
| 11a.4 | RBAC tier extension for entity-tier summary | Kasra | 0.25d | G85 |
| 11a.5 | Tests + accessibility + opacity-on-stale rendering | Kasra | 0.5d | none |

Total: ~2.75 engineer-day equivalents. Slots into Phase 6 as Stage 6e+
(after cross-tier rendering ships).

---

## 5. Acceptance criteria

1. Ron logs in at `/people/ron-oneil`, sees his Standing panel: current
   μ + σ + 30-day sparkline + top-5 capabilities + last 10 outcomes.
2. The `accepted_or_skipped` column on the reputation history page
   shows at least one row per filter as "skipped — below inertia
   threshold" once §10a is live (verifies §10a's behaviour is visible
   to the subject).
3. Capabilities with `last_reinforced_at` >30 days render at reduced
   opacity (visual indicator of high `flow_inertia`).
4. Hadi viewing Ron at entity-tier sees only the Reputation panel
   summary (no event log, no projections).
5. Public URL `/people/ron-oneil` does not render Standing panel at
   all — passes RBAC tier gate.
6. No regression on §11 profile-rendering tests.

---

## 6. Open questions

- **Q1:** Sparkline timeframe — 30 days vs 90 days? 30 days is the
  default Glicko-2 period; aligns with §10a decay timescale. Recommend
  30 default with self-toggle to 90.
- **Q2:** Canonical lambda_dna clusters need seeding. Loom + Athena
  identify N initial clusters (e.g. infrastructure-track, content-
  track, sales-track, security-track) and label them. Seed list lives
  in `mirror/migrations/04X_seed_lambda_dna_clusters.sql`. Defer the
  seed list to Phase 6 build kickoff; placeholder in the spec.
- **Q3:** Does this surface Glicko-2 σ to the citizen? Recommend: yes
  as the confidence interval ("1450 ± 120"). It tells them how much
  the system trusts the score, which is honest. If σ is large, the
  score is volatile — the citizen sees that volatility, not just the
  point estimate.

---

## 7. Versioning

| Version | Date       | Change                                              |
|---------|------------|-----------------------------------------------------|
| v0.1    | 2026-04-25 | Initial draft on `loom` branch — Loom autonomous   |
|         |            | extension of §11 to surface §10a reputation +      |
|         |            | lambda_dna position at self-tier profile. Closes   |
|         |            | §11 Q6 (Phase 6 kickoff brief open question).      |

---

## 8. Why this matters (one-paragraph rationale)

§11's transparency principle requires the system to show citizens what
it knows about them. Reputation and lambda_dna are among the most
load-bearing pieces the substrate maintains — they determine matchmaking
outcomes that materially affect the citizen's economic standing. Hiding
them creates an information asymmetry that the rest of §11 explicitly
rejects (`every access is logged`, `consent is first-class`,
`transparency by default`). §11a surfaces them at self-tier with
inertia-aware visualization (stale capabilities fade, gated outcomes
show as skipped) so citizens see *both* their current standing and the
substrate's confidence in it. Entity-tier viewers get a summary for
operational decisions but not the full history — keeps the principle
that the citizen's story belongs to them.
