# SOC 2 Type I Evidence Package

**Generator:** `python -m sos.jobs.evidence_package`
**Gate:** G65 (Sprint 006 Track C.2)
**Companion doc:** `../soc2-cc-mapping.md`

## What this directory contains

Each `evidence-YYYY-MM-DD.json` file is a self-contained audit evidence
snapshot with four sections:

| Section | Controls substantiated | SOC 2 criteria |
|---------|------------------------|----------------|
| `chain_integrity` | Hash-chained audit log with verified linkage | CC1.5, CC4.1, CC7.2 |
| `chain_samples` | Sample audit_events rows (with hash_hex fields) | CC1.5, CC4.2 |
| `r2_anchor_proofs` | WORM R2 anchor objects confirming 7-year retention | CC1.5, CC4.1 |
| `test_run_summary` | Pytest pass counts for MFA/SSO/SCIM/DEK/RBAC | CC6.1, CC6.2, CC6.3 |

## How to regenerate

```bash
cd ~/SOS
# Point to the correct database and R2 credentials:
source .env && source .env.supabase

python -m sos.jobs.evidence_package
# Writes docs/compliance/evidence/evidence-<today>.json
```

Run without R2 credentials for a chain-only run (r2_anchor_proofs section
will be marked as skipped with a note):

```bash
python -m sos.jobs.evidence_package --out /tmp/evidence-local.json
```

## Interpretation notes

- `chain_integrity.ok = true` means every event in every stream has a valid
  SHA-256 link to its predecessor — no event has been silently inserted,
  deleted, or modified.
- `r2_anchor_proofs.total_objects` grows by 1 per active stream every 15
  minutes (the anchor timer cadence). The presence of objects confirms the
  WORM write path is live.
- `test_run_summary` reflects the state of the codebase at generation time.
  `passed` counts are the primary signal; `skipped` rows are DB-requiring
  tests that are skipped in environments without live Supabase credentials.
- All timestamps are UTC ISO 8601.

## Versioning

| Date | Notes |
|------|-------|
| 2026-04-25 | Initial generator — Sprint 006 C.2 (G65). |
