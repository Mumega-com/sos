# Runbook — Audit WORM Bucket v2 Migration (Sprint 006 A.1b / G51)

**Status:** Built (c111fe52, 2026-04-25). Pending gate (Athena + adversarial).  
**Note:** Build was executed before brief v0.1 was written (ordering mismatch). Gate retroactively validates.

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
