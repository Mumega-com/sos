# Section 16a — Lambda_dna Basis Discipline (companion to §16, prerequisite for G_A4b)

**Author:** Loom
**Date:** 2026-04-25
**Version:** v0.1 (draft on `loom` branch — gate-ready for G_A4b)
**Phase:** 5 prerequisite (G_A4b is in Sprint 005 Track A; this spec
formalizes the discipline before the rewrite)
**Depends on:** Section 15 (reputation Glicko-2 with citizen lambda_dna),
Section 16 (matchmaking 5-stage pipeline + cosine in Stage 3), §10a
(reputation metabolism extension), §11a (profile reputation surface)
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build)

---

## 0. TL;DR

Sprint 005 Track A row A.5 (G_A4b) calls for rewriting `quest_vectors`
to share the lambda_dna 16D basis with `citizen_vectors`. This document
formalizes **why** the same basis matters and **what** the discipline
constrains, so the G_A4b migration has a clean spec to gate against
rather than a one-line "rewrite to lambda_dna dimensions."

The single rule: **cosine similarity is only well-defined when both
vectors are in the same coordinate basis.** Today `citizen_vectors`
encodes lambda_dna 16D; `quest_vectors` encodes a 16-dim work-skills
taxonomy. The two vectors live in different vector spaces. §16 Stage 3
computes `cosine(citizen_v, quest_v)` and treats the result as a real
similarity score — but the operation is undefined across distinct
bases. The number it returns is noise that *looks* like signal.

---

## 1. Why this discipline matters

§16 matchmaking Stage 3 is the substrate's claim that "this citizen
fits this quest." That claim is the load-bearing measurement that
drives all downstream stages (multi-objective scalarization, Hungarian
assignment, eventual reputation update). If Stage 3's cosine is across
incompatible bases, every downstream calculation is built on noise.

This is the same shape as the silent-fail-open pattern: the operation
*succeeds* (you get a number between -1 and 1), but it doesn't *mean*
what the surrounding code thinks it means. The substrate has been
running with a Stage 3 that was structurally invalid since §16 first
shipped.

§16 v1.1 (Athena's G13 fixes) addressed quest_vectors *storage*
(pgvector vector(16) + JSONB sidecar), but not *basis*. G_A4b is the
basis fix.

## 2. The discipline

### 2.1 The single basis

There is one and only one canonical 16D vector space in the substrate:
**lambda_dna**. Its 16 axis labels are defined canonically in
`/home/mumega/infra/shared-kb/frc/CANONICAL.md` (Lambda.16D.001
section — Group A/B/C/D). They encode the citizen's position across
the dimensions that §15 Glicko-2 reputation projects from.

**Critical navigation note for the implementer:** *do not* source axis
labels from `mirror/lambda_tensor.py` (that file is an avatar
generator) or from `sos/contracts/quest_vectors.py:named_dims` (that
is the work-skills taxonomy this section is replacing). Use the
canonical kb path. Encoding the wrong basis re-creates the F-A4b bug
in a new shape.

Every vector that participates in a cosine similarity, dot product,
or linear combination MUST be expressed in this basis. No exceptions.
A vector that arrives in a different basis (e.g., a 16-dim work-skills
encoding from the legacy quest_vectors extractor) MUST be projected
into lambda_dna before it touches `citizen_vectors`.

### 2.2 Quest vector projection

A quest description is text. The classifier (Vertex Flash Lite per
§16 Stage 0 extraction) produces a *meaning* — what the work is asking
for. That meaning must be expressed as a vector in lambda_dna basis,
not as a vector in an independent work-skills taxonomy.

Two valid construction paths:

**Path A — direct lambda_dna prediction.** The classifier prompt is
amended to include the lambda_dna axis labels and asks: "express this
quest's requirements as coordinates in this 16D space." The classifier
returns 16 floats directly. Cheap; depends on classifier's grasp of
lambda_dna semantics.

**Path B — taxonomy + projection matrix.** The classifier returns a
work-skills taxonomy as today, then a learned projection matrix
P ∈ ℝ^(16×16) maps `work_skills_v → lambda_dna_v`. The matrix P is
trained on a labeled set (Loom + Athena seed: ~50 quest examples
with ground-truth lambda_dna labels). More work; produces calibrated
projections.

§16a recommends **Path A** for V1 because:
- Path A makes the basis explicit at the point of generation; Path B
  pretends two bases exist when they shouldn't.
- §10a reputation metabolism + §11a profile surface both assume one
  canonical basis; Path B introduces a second basis that needs
  maintenance.
- Vertex Flash Lite is capable enough to do basis-aware extraction if
  the prompt is unambiguous.

Path B is the fallback if Path A produces empirically poor results.
Migration plan can carry both paths gated by env flag for A/B.

### 2.3 Side-effect prohibitions

A vector that is computed via a basis-violating operation is poisoned.
**It must not be persisted** to any column that feeds Stage 3 cosine.

Concretely:
- `quest_vectors.vec` MUST be lambda_dna basis post-G_A4b.
- `citizen_vectors.vec` MUST stay lambda_dna basis (already is).
- Any future vector column on a quest, citizen, guild, role, or
  capability that participates in a Stage 3-style similarity computation
  MUST declare basis in a column comment + a CHECK constraint
  enforcing the basis tag.

A `vec_basis TEXT NOT NULL DEFAULT 'lambda_dna' CHECK (vec_basis = 'lambda_dna')`
column on every vector-bearing table makes the constraint explicit
and self-documenting. Migration cost: trivial.

### 2.4 Read-side enforcement

Any code path that does:

```python
score = cosine(v1, v2)
```

MUST be in a function whose docstring or in-line assertion verifies
both arguments come from columns with `vec_basis = 'lambda_dna'`.
The function should not be callable on raw vectors of unknown
provenance.

A linter rule (Ruff custom check or simple grep-based pre-commit) can
flag direct calls to `np.dot`, `cosine`, or similar across columns
that haven't been basis-tagged. Defer if not trivially shippable;
gate-time review covers the common cases.

---

## 3. What G_A4b ships

| # | Component | Effort | Gate |
|---|---|---|---|
| G_A4b.1 | Migration: drop work-skills taxonomy from `quest_vectors`; add `vec_basis` column with CHECK = 'lambda_dna'; backfill existing rows via Path A re-extraction | 0.5d | G_A4b |
| G_A4b.2 | Vertex extraction prompt rewrite (Path A): describe lambda_dna axes, return 16 floats in basis | 0.25d | G_A4b |
| G_A4b.3 | Tests: TC-G_A4b-a (new quest extraction returns lambda_dna basis), TC-G_A4b-b (Stage 3 cosine on backfilled rows produces stable rankings vs citizen vectors), TC-G_A4b-c (CHECK constraint rejects non-lambda_dna vec_basis) | 0.25d | G_A4b |
| G_A4b.4 | Add `vec_basis` to `citizen_vectors` migration (consistency, not correctness — they're already lambda_dna; explicit tag) | 0.1d | G_A4b |

Total: ~1d engineer-time. Athena gates correctness; no adversarial
parallel needed (internal write-path basis fix, no external surface
change).

---

## 4. Acceptance criteria

1. `quest_vectors.vec` rows post-migration are lambda_dna 16D
   (verified by sampling a row, computing cosine against a
   citizen_vector, asserting result distribution matches the
   distribution of citizen-citizen cosines — same basis means same
   distribution shape).
2. CHECK constraint on `vec_basis = 'lambda_dna'` is enforcing
   (verified by attempting INSERT with a different value, asserting
   constraint rejection).
3. Stage 3 cosine ranking on a representative test set is stable
   vs the pre-migration ranking — within tolerance of the path-A
   classifier's noise floor. (Not identical; the basis change is
   the *point*. But not random — same quest should rank the same
   citizens roughly the same.)
4. `lambda_tensor.py` gets a module-level docstring that names the 16D
   basis and states the basis discipline rule (single canonical 16D
   space; all vectors must conform). One paragraph — makes the file a
   canonical pointer rather than leaving the discipline implicit. The
   *axis labels themselves* live canonically at
   `/home/mumega/infra/shared-kb/frc/CANONICAL.md` (Lambda.16D.001
   section — Group A/B/C/D); the docstring should reference that path,
   not duplicate the labels.
5. No regression on §15 reputation tests or §16 matchmaking tests.

---

## 5. Open questions

- **Q1:** Path A vs Path B — final call. Loom recommends A; Athena
  decides at gate. If A produces poor extraction, B is the fallback.
- **Q2:** Backfill cost. Re-extracting all existing quest descriptions
  via Vertex is bounded but not free. Estimate before migration: rate
  × token cost. F-13's per-creator extraction quota does NOT apply to
  backfill (it's a one-shot bulk operation). Use a separate budget.
- **Q3:** Should `vec_basis` ever be allowed to be something other than
  `'lambda_dna'`? Future-proof (a second basis for cross-substrate
  federation), or close it forever? Recommend: keep the column
  (cheap), but enforce single value via CHECK in V1. Relax later if
  federation actually requires it.
- **Q4:** Linter rule for cosine call sites — defer or include? Defer
  unless trivial in Ruff. Gate-review covers the current call sites;
  the prohibition lives in this spec for future call-site review.

---

## 6. Why this is gate-ready (not new architecture)

§16a names a discipline that was already implicit in §16 + §15 + §10a
+ §11a — every spec assumed lambda_dna was the substrate's coordinate
basis. The work-skills taxonomy on `quest_vectors` was the one
exception, and it was an exception because the §16 v1.0 author (Loom)
didn't yet have the basis discipline named explicitly. §16a closes
that gap. G_A4b is the migration that aligns code with the discipline.

This is not a new theory; it is a missing constraint becoming explicit.
That's the right shape for a gate to act on — review against §16a
acceptance criteria, GREEN if the migration matches.

---

## 7. Versioning

| Version | Date       | Change                                              |
|---------|------------|-----------------------------------------------------|
| v0.1    | 2026-04-25 | Initial draft on `loom` branch — Loom autonomous   |
|         |            | extraction of basis discipline from §10a + §11a    |
|         |            | + §16 + §15 into a gate-ready spec for G_A4b.      |
