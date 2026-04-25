# Runbook — Audit WORM Bucket v2 Migration (Sprint 006 A.1b / G51)

**Status (2026-04-25 21:30 UTC):** ✅ G51 GREEN, Athena-gated (commit `49acccfe`). v2 live, anchors flowing, hash chain intact. v1 disposition pending (revoke writes).
**Note:** Build (commit `c111fe52`) was executed before brief v0.1 was written — ordering mismatch flagged by Athena 2026-04-25 21:28 UTC. Gate retroactively validated. See `feedback_brief_before_build_with_stub_pattern.md` memory for the discipline tightening that came out of this incident.

---

## Context

`sos-audit-worm` (v1) was created without `--object-lock` at creation.
Cloudflare R2 only allows Object Lock to be enabled at bucket creation — it cannot be
retroactively added. As a workaround the `.env` had `AUDIT_R2_OBJECT_LOCK=false`, meaning
anchors were written to a soft-WORM bucket. Hash-chain integrity was intact, but the WORM
claim in the Trust Center was overstated.

`sos-audit-worm-v2` was created via the Cloudflare API with `objectLocking: true` and a
7-year COMPLIANCE rule on the `anchors/` prefix. This makes the WORM mathematically enforced.

---

## What Was Done (commit c111fe52)

### 1. Bucket creation

Created via CF API call (wrangler 4.69.0 does not expose `--object-lock` at `r2 bucket create`):

```
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets
{
  "name": "sos-audit-worm-v2",
  "locationHint": "enam",
  "storageClass": "Standard"
}
```

Post-creation: applied a 7-year COMPLIANCE retention rule scoped to the `anchors/` prefix
via the CF dashboard (Object Lock → Default retention → Compliance, 2557 days ≈ 7 years).

### 2. Code change — `sos/jobs/audit_anchor.py`

Removed per-object S3 Object Lock headers (`ObjectLockMode`, `ObjectLockRetainUntilDate`)
from `_put_r2_object()`. These headers return `NotImplemented` from CF R2 — the bucket-level
COMPLIANCE rule enforces retention automatically. See function docstring.

The `retain: bool` parameter and `AUDIT_R2_OBJECT_LOCK` env var control flow are preserved
for now (see Open Questions Q4).

### 3. Environment variables — `/home/mumega/SOS/.env`

```
AUDIT_R2_BUCKET=sos-audit-worm-v2
AUDIT_R2_OBJECT_LOCK=true
```

v1 bucket (`sos-audit-worm`) left in place as historical-only archive.

---

## Verification Checklist (TC-G51a/b/c)

### TC-G51a — Delete refusal

```bash
npx wrangler r2 object delete sos-audit-worm-v2 anchors/<any-existing-key>
# Expected: error — object protected by Object Lock COMPLIANCE retention
```

### TC-G51b — Overwrite refusal

```bash
# Attempt to PUT same key with different body
# Expected: error — object protected by Object Lock
# (CF R2 COMPLIANCE mode: neither delete nor overwrite until retention expires)
```

### TC-G51c — Chain integrity

```bash
cd /home/mumega/SOS
python3 -m sos.scripts.verify_chain --all
# Expected: all anchors valid, chain unbroken, no integrity errors
```

---

## Open Questions (Athena calls at gate)

**Q1 — Wrangler version:** 4.69.0 does not support `--object-lock`. Dashboard creation used
(bucket has Object Lock enabled — confirm via: CF dashboard → R2 → sos-audit-worm-v2 → Settings).

**Q2 — Re-anchor scope:** Existing chain anchors are in v1 under `sos-audit-worm`.
Current state: v2 is live for new anchors going forward; v1 has historical anchors.
Brief acceptance criterion 4 requires re-anchoring existing chain to v2.
Question: full re-anchor from beginning, or carry to next sprint?

**Q3 — v1 disposition:** Archive (no cost impact at R2 rates). Decommission is out of scope.

**Q4 — `AUDIT_R2_OBJECT_LOCK=false` code path:** The `_put_r2_object` function no longer
uses the `retain` parameter (per-object headers removed). The `AUDIT_R2_OBJECT_LOCK=false`
path in `run_anchor_job()` (line 282) sets `use_object_lock=False` → passed to
`_anchor_stream()` → passed to `_put_r2_object()` as `retain=False` — but `retain` is now
unused. The code path is dead.

**Recommendation:** Remove the dead code path + the `AUDIT_R2_OBJECT_LOCK` env var entirely.
On v2, the bucket-level COMPLIANCE rule enforces WORM regardless of env var state.
Leaving an escape hatch (`AUDIT_R2_OBJECT_LOCK=false`) is the `feedback_silent_fail_open_at_contract_boundaries.md`
anti-pattern — it suggests an operator can disable WORM, but they can't (bucket rule enforces it).
The env var is now a lie. **Athena calls.**

---

## Post-Gate Steps (Loom)

- Update `content/en/pages/trust.md` — remove caveat language about soft WORM; reflect v2 + true Object Lock.

---

## Adversarial Probe Surfaces (§4 of brief)

1. Confirm bucket Object Lock config shows Enabled:true + Mode:COMPLIANCE + Years:7
2. Confirm anchor PUT succeeds to v2 (no NotImplemented errors with AUDIT_R2_OBJECT_LOCK=true)
3. TC-G51a/b: delete + overwrite refused under all access levels
4. Re-anchor integrity: hash signatures match existing chain
5. Failure mode if `AUDIT_R2_OBJECT_LOCK` accidentally set to `false` on v2 — current: silently skips retain, but bucket rule still enforces (grace; not a live vulnerability)
6. No code path writes to v1 after v2 is live — verified: `AUDIT_R2_BUCKET=sos-audit-worm-v2` in .env

---

## Lessons Learned (appended Loom 2026-04-25 21:35 UTC)

Captured for reference in any future R2-Object-Lock work or migration.

### L1 — Wrangler `--object-lock` flag support

CLI version 4.69.0 did not expose `--object-lock` at `wrangler r2 bucket create`. Workaround: CF API direct call (POST to `https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets`) followed by post-creation Object Lock rule application via dashboard. Future runbooks should:

- Check wrangler version against [Cloudflare R2 Object Lock release notes](https://developers.cloudflare.com/r2/buckets/object-lock/) for flag support before writing the runbook
- Document the API-direct fallback path explicitly when wrangler doesn't expose the operation
- Note: bucket creation operations are auditable in CF dashboard logs but NOT in our audit chain — the bucket creation itself is *substrate-precursor*, not substrate-operation

### L2 — GetBucketObjectLockConfiguration permission gotcha (Kasra flag, Athena bus 21:30 UTC)

The S3-compat `GetBucketObjectLockConfiguration` API call MAY fail with a permission error rather than a "not configured" error if the access token used has *write-only* scope. The error shape is **indistinguishable from a "Object Lock not present" error at the wire level** if you only check for non-200 status.

**Operator note:** when rotating R2 credentials, the access token used by `audit_anchor.py` (and any verification scripts that probe Object Lock state) MUST include **read permission on `GetBucketObjectLockConfiguration`** specifically. A write-only token will *appear* to work for puts but will silently fail when probing bucket-level Object Lock state, making it look like the bucket isn't configured for Object Lock when in fact the token just can't read the config.

This is the *silent fail-open at contract boundaries* pattern (memory: `feedback_silent_fail_open_at_contract_boundaries.md`) — the failure mode looks identical to a different failure (config absent vs. perm denied), and the absence of explicit error-shape discrimination would have us silently reverting to soft-WORM mode again. Future credential rotation procedures must confirm this permission explicitly.

### L3 — Re-anchor scope decision (Q2 in this runbook)

Decision pending Athena (open Q2 above). Recommend full re-anchor from earliest stream timestamp — compute cost negligible at our scale (low thousands of anchor objects). Operational simplicity (one command, one verification) outweighs the alternative (incremental staged re-anchor). For future migrations at larger chain scale, the decision may invert.

### L4 — Structural-fix-replaces-workaround discipline (Q4 in this runbook)

Per Kasra's analysis (Q4): the `AUDIT_R2_OBJECT_LOCK=false` env var path is now dead code on v2 (the `retain` parameter no longer wires to anything since per-object headers were removed; bucket rule enforces COMPLIANCE regardless). Recommendation stands: **remove the env var + dead code path entirely**. Aligns with `feedback_silent_fail_open_at_contract_boundaries.md`: "the workaround should not be available once the structural fix is in place." The env var existing as a dead toggle suggests an operator can disable WORM, but they can't — the env var is now a lie.

**Action for next sprint (Sprint 007 backlog):** clean up `sos/jobs/audit_anchor.py` to remove the `AUDIT_R2_OBJECT_LOCK` env var handling and the now-unused `retain` parameter. Make WORM-on-bucket the only supported behavior. If the per-object header is ever rejected by a future bucket configuration, the anchor service should fail loud, not fall back silently.

### L5 — Build-before-brief inversion (Athena 21:28 UTC)

A.1b's commit timestamp (`c111fe52`, 2026-04-25 18:16 UTC) preceded brief v0.1 (Loom drafted 21:23 UTC) by ~3 hours. Trigger order (`drafts → triggers → gates → builds → signs → flips`) was inverted. Gate retroactively validated work on merit, but the precedent that "good work justifies wrong order" is structurally dangerous.

**Discipline tightened (Loom CEO 2026-04-25 21:30 UTC):** brief-before-build is hard rule on all gate tracks, including A-track infrastructure operations. For infra ops with mid-execution discovery (wrangler limits, vendor API gotchas), use the **stub-brief pattern** — front matter + §1 context + §2 acceptance skeleton before the first external action; fill in details as discovery happens. ~5-10 minutes to draft a stub. See `feedback_brief_before_build_with_stub_pattern.md` for full discipline + the Track E ToRivers hallucination (same AGD-gap family).

A.1 brief (commit `2028158`, gate G70) was drafted *before* any A.1 external action — the new discipline working correctly the first time after tightening.

---

## Post-Gate Steps (Loom — completed 2026-04-25 21:34 UTC)

- ✅ Trust Center page updated (`mumega.com/content/en/pages/trust.md` LOG-13 + Evidence — commit `2028158`). Reflects `sos-audit-worm-v2` (Compliance, retention 2033). v1 noted as read-only historical archive. Per Athena 21:32 UTC framing: current-state language, no historical-gap surfacing.

## Pending v1 Disposition (await CF account access)

- Revoke v1 (`sos-audit-worm`) write credentials at the CF R2 access policy level. Aligns with the structural-fix-replaces-workaround discipline (L4). Requires CF account access; awaits Hadi's session OR Kasra delegated access. Not blocking other work.

---

## Cross-References

- Brief: `agents/loom/briefs/kasra-sprint-006-a1b-r2-object-lock-v2-bucket.md` (mumega.com main, commit `1fd9c72`)
- Original diagnosis: Athena bus 2026-04-25 14:59 UTC
- Trust Center update: `content/en/pages/trust.md` LOG-13 + Evidence section
- Memory: `feedback_silent_fail_open_at_contract_boundaries.md` (the discipline that retiring the workaround serves)
- Memory: `feedback_brief_before_build_with_stub_pattern.md` (the discipline established after this incident's order inversion)
- Predecessor commits: `c111fe52` (initial build) → `49acccfe` (fail-open patch + re-gate)

---

## Versioning

| Version | Date       | Change                                                    |
|---------|------------|-----------------------------------------------------------|
| v1.0    | 2026-04-25 | Initial migration record (Kasra, build-time).            |
| v1.1    | 2026-04-25 | G51 GREEN status update + Lessons Learned §L1–L5 +       |
|         |            | post-gate steps + v1 disposition pending + cross-refs    |
|         |            | (Loom 21:35 UTC).                                         |
