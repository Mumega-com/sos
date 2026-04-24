# Section 2 — GAF Compliance Fixes (Critical — Gates Next Customer Onboarding)

**Author:** Loom  
**Date:** 2026-04-24  
**Status:** Draft — awaiting Kasra review  
**Priority:** CRITICAL — must ship before next GAF customer onboards  
**Source:** `customers/gaf/05-compliance-audit.md`

---

The compliance audit surfaced seven gaps that individually weaken GAF's CRA-audit defensibility and together make the current binder architecture decorative rather than cryptographically binding. This section converts those findings into a sequenced fix plan with enough code-level detail for Kasra to execute without re-reading the audit.

---

## 2A. Merkle Root Computation + Write at Lock

**Problem.** `audit_binders.merkle_root` exists in schema (`0040_audit_trail_bonding.sql`) and in the `AuditBinder` TypeScript type, but no application code ever writes it. The submit route at `src/routes/cases.ts:567–624` is commented out entirely. A binder "locked" today has `merkle_root = NULL` — the cryptographic anchor that proves what the binder contained at filing time does not exist. CRA cannot verify binder state at submission versus current state.

**Fix shape.**

Implement `computeMerkleRoot(binderId: string): Promise<string>` in `engine/bonding.ts`:

```typescript
export async function computeMerkleRoot(binderId: string): Promise<string> {
  const { data: items } = await supabase
    .from('evidence_items')
    .select('evidence_hash')
    .eq('binder_id', binderId)
    .not('evidence_hash', 'is', null)
    .order('evidence_hash', { ascending: true }) // canonical sort

  if (!items || items.length === 0) {
    throw new ComplianceError('Cannot compute Merkle root: no bonded evidence')
  }

  const leaves = items.map(i => i.evidence_hash as string)
  return buildMerkleRoot(leaves) // existing helper in engine/bonding.ts
}
```

Uncomment and rewrite the submit route (`src/routes/cases.ts:567–624`):

```typescript
app.post('/cases/:id/submit', requireAuth, async (c) => {
  const { id: caseId } = c.req.param()
  const { workspaceId } = getSession(c)

  // 1. Verify all evidence is bonded
  const { data: unbonded } = await supabase
    .from('evidence_items')
    .select('id')
    .eq('case_id', caseId)
    .is('evidence_hash', null)
  if (unbonded?.length) {
    return c.json({ error: 'unbonded_evidence', count: unbonded.length }, 422)
  }

  // 2. Verify practitioner signoff exists (see 2C)
  const { data: signoff } = await supabase
    .from('practitioner_signoffs')
    .select('id')
    .eq('case_id', caseId)
    .single()
  if (!signoff) {
    return c.json({ error: 'missing_practitioner_signoff' }, 422)
  }

  // 3. Compute Merkle root from binder
  const binder = await getBinderForCase(caseId, workspaceId)
  const rootHash = await computeMerkleRoot(binder.id)

  // 4. Lock binder atomically
  await supabase.from('audit_binders').update({
    merkle_root: rootHash,
    status: 'locked',
    locked_at: new Date().toISOString(),
  }).eq('id', binder.id)

  // 5. Transition case to submitted
  await transitionCaseStatus(caseId, 'submitted')

  // 6. Emit binder_locked event on bus
  await emitEvent('binder_locked', { caseId, binderId: binder.id, rootHash, workspaceId })

  return c.json({ status: 'submitted', merkle_root: rootHash })
})
```

**DB-level row lock.** Add a Postgres trigger that rejects evidence mutations once a binder is locked:

```sql
-- Migration: 0062_lock_evidence_on_binder_lock.sql
CREATE OR REPLACE FUNCTION reject_evidence_mutation_on_locked_binder()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM audit_binders
    WHERE id = NEW.binder_id AND status = 'locked'
  ) THEN
    RAISE EXCEPTION 'evidence_items is immutable: binder % is locked', NEW.binder_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_evidence_immutable_on_lock
  BEFORE UPDATE OR INSERT ON evidence_items
  FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation_on_locked_binder();
```

**Test plan.**
- `merkle_root` is non-null after submit; value matches `computeMerkleRoot` called again on same data (round-trip).
- `status = 'locked'` and `locked_at` is set.
- `INSERT` or `UPDATE` on `evidence_items` where `binder_id` references a locked binder raises exception.
- Submit with any unbonded evidence returns 422 `unbonded_evidence`.

**Owner:** Kasra | **Effort:** 1 day | **Blockers:** None — 2C must ship first (practitioner signoff gate).

---

## 2B. Source-Timestamp Integrity

**Problem.** `evidence_items.created_at` is set to `now()` at ingest. A 2023 GitHub commit batch-ingested in 2026 appears with `created_at = 2026` and is classified as contemporaneous evidence for an April 2026 case. CRA examiners who pull raw GitHub API data will find the dates are fabricated by the ingest pipeline. The compliance scorer compounds the problem: `calculateComplianceScore` uses `created_at` for its 30-day density window, not the fiscal year of the SR&ED claim.

**Fix shape.**

Migration to add `source_timestamp`:

```sql
-- Migration: 0063_evidence_source_timestamp.sql
ALTER TABLE evidence_items
  ADD COLUMN source_timestamp TIMESTAMPTZ;

-- Backfill from metadata_json where available
UPDATE evidence_items
SET source_timestamp = (metadata_json->>'authored_at')::TIMESTAMPTZ
WHERE metadata_json->>'authored_at' IS NOT NULL;

-- Flag rows that cannot be backfilled
UPDATE evidence_items
SET metadata_json = metadata_json || '{"backfill_required": true}'::jsonb
WHERE source_timestamp IS NULL;
```

Each adapter populates `source_timestamp` from the source record:

```typescript
// adapters/github.ts
evidence.source_timestamp = commit.author.date   // ISO 8601 from GitHub API

// adapters/qbo.ts
evidence.source_timestamp = transaction.txn_date

// adapters/slack.ts
evidence.source_timestamp = new Date(parseFloat(message.ts) * 1000).toISOString()

// adapters/manual.ts — user-supplied date, validated to be in the past
evidence.source_timestamp = body.source_date
```

`compliance.ts:calculateComplianceScore` uses `source_timestamp` (not `created_at`) for temporal weighting, and windows against the fiscal year of the case — not the last 30 days:

```typescript
const fiscalStart = new Date(`${fiscalYear}-01-01`)
const fiscalEnd   = new Date(`${fiscalYear}-12-31`)
const isContemporaneous = (item: EvidenceItem) =>
  item.source_timestamp >= fiscalStart && item.source_timestamp <= fiscalEnd
```

`dossier.ts` presents `source_timestamp` as the evidence date field to CRA:

```typescript
verification_date: item.source_timestamp ?? item.created_at, // fallback flagged
```

**Migration note.** Any row where `source_timestamp` remains NULL after backfill is flagged with `backfill_required: true` in `metadata_json`. These rows must be reviewed by the operator before the binder can be locked (add check to submit route pre-flight).

**Test plan.**
- A 2023 GitHub commit ingested in 2026: `source_timestamp = 2023-*`, `created_at = 2026-*`.
- Compliance score for a fiscal-year-2024 case uses 2023 commits as contemporaneous; ignores 2026-only evidence.
- Submit pre-flight rejects binders with unflagged `backfill_required` rows.
- `dossier.ts` chain-of-evidence uses `source_timestamp` not `created_at`.

**Owner:** Kasra | **Effort:** 1 day | **Blockers:** None.

---

## 2C. Break the AI-Verification Loop

**Problem.** The Developer Champion 5-point sign-off is a closed AI loop: AI synthesizes a narrative from commits → AI extracts structured fields (`constraint_description`, `knowledge_gap`, `hypothesis`, etc.) from that narrative → one developer is auto-assigned → developer clicks verify. CRA's SR&ED criteria require a qualified person to independently attest to technological uncertainty and systematic investigation. Fields written by the AI that wrote the narrative, verified by the developer the AI wrote about, constitute self-attestation — not independent human attestation. This collapses under cross-examination.

**Partnership fix (relationship-owned by Hadi — external dependency).** Contract with a Boast, Leyton, or independent CPA / SR&ED consultant as a practitioner partner. The practitioner signs the T661 attestation. GAF's role is dossier delivery; a qualified human approves and files. This dependency must be resolved before the code gate in 2A unlocks (the submit route requires a `practitioner_signoffs` row). **Flag as BLOCKED on partnership relationship — Hadi to close.**

**Code fix.** Remove AI auto-population of Developer Champion fields. Replace with a structured form that a qualified human fills in. No AI pre-population. No auto-assignment.

Remove from `routes/scans/binders-drafting.ts:40–66`:
```typescript
// DELETE: OpenAI extraction of Developer Champion fields
// DELETE: auto-assignment of developer to verification task
```

Replace with a structured input form with five required human-entered fields. The developer who submits the narrative cannot be the same person who completes the Champion attestation — a second qualified person (practitioner partner or CDA) is required.

**Schema:**

```sql
-- Migration: 0064_practitioner_signoffs.sql
CREATE TABLE practitioner_signoffs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id          UUID NOT NULL REFERENCES cases(id),
  practitioner_id  UUID NOT NULL REFERENCES user_accounts(id),
  narrative_author_id UUID NOT NULL REFERENCES user_accounts(id),
  sigh_hash        TEXT NOT NULL,  -- SHA-256 of signoff content
  signed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  attestation_text TEXT NOT NULL CHECK (char_length(attestation_text) > 100),
  CONSTRAINT practitioner_not_author
    CHECK (practitioner_id != narrative_author_id)
);

CREATE INDEX idx_practitioner_signoffs_case ON practitioner_signoffs(case_id);
```

**Test plan.**
- Cannot lock binder without a `practitioner_signoffs` row for the case.
- `practitioner_id` must differ from `narrative_author_id` — constraint enforced at DB level.
- `attestation_text` must be non-empty and exceed 100 characters.
- Developer Champion fields are not pre-populated from AI output; form returns empty for these fields.

**Owner:** Kasra (code) + Hadi (partnership) | **Effort:** 1 day code, 2–3 days partnership | **Blockers:** Partnership relationship required before submit gate can be exercised end-to-end.

---

## 2D. Forensic Audit Log Chain

**Problem.** `logForensicAudit` hashes each entry's content in isolation — there is no `previous_hash` linking entries. The table is append-only but the sequence has no integrity guarantee. CRA counsel cannot verify that entries appear in their original order, and an adversary can delete a row without breaking any chain. The forensic log is not forensic.

**Fix shape.**

Migration to add `previous_hash`:

```sql
-- Migration: 0065_forensic_audit_chain.sql
ALTER TABLE forensic_audit_logs
  ADD COLUMN previous_hash TEXT,
  ADD COLUMN sequence_num  BIGINT GENERATED ALWAYS AS IDENTITY;

CREATE INDEX idx_forensic_workspace_seq ON forensic_audit_logs(workspace_id, sequence_num);
```

Update `logForensicAudit` to include `previous_hash` in each entry's hash input:

```typescript
export async function logForensicAudit(entry: ForensicEntry): Promise<void> {
  const { data: prev } = await supabase
    .from('forensic_audit_logs')
    .select('merkle_hash')
    .eq('workspace_id', entry.workspaceId)
    .order('sequence_num', { ascending: false })
    .limit(1)
    .single()

  const previousHash = prev?.merkle_hash ?? '0'.repeat(64) // genesis sentinel

  const content = JSON.stringify({
    ...entry,
    previous_hash: previousHash,
  })
  const merkleHash = await sha256(content)

  await supabase.from('forensic_audit_logs').insert({
    ...entry,
    previous_hash: previousHash,
    merkle_hash: merkleHash,
  })
}
```

Verification endpoint:

```typescript
app.get('/forensic-audit/verify', requireAdminAuth, async (c) => {
  const { workspace_id } = c.req.query()
  const { data: logs } = await supabase
    .from('forensic_audit_logs')
    .select('*')
    .eq('workspace_id', workspace_id)
    .order('sequence_num', { ascending: true })

  let prevHash = '0'.repeat(64)
  const breaks: number[] = []

  for (const log of logs ?? []) {
    const expected = await sha256(JSON.stringify({ ...log, previous_hash: prevHash }))
    if (expected !== log.merkle_hash) breaks.push(log.sequence_num)
    prevHash = log.merkle_hash
  }

  return c.json({ chain_intact: breaks.length === 0, breaks, total: logs?.length })
})
```

**Test plan.**
- Sequential inserts produce unbroken chain; `GET /forensic-audit/verify` returns `chain_intact: true`.
- Manual deletion of a middle row causes `chain_intact: false` with the deleted row's sequence number in `breaks`.
- Genesis entry has `previous_hash = '000...000'`.

**Owner:** Kasra | **Effort:** 0.5 day | **Blockers:** None.

---

## 2E. Cross-Tenant Isolation Test

**Problem.** Binder queries filter by `workspace_id` derived from the authenticated session, and the audit confirms this is enforced in `binders-core.ts`, `binders-sync.ts`, and `routes/cases.ts`. However, no test explicitly verifies that workspace A cannot read or mutate workspace B's evidence, binders, or forensic logs. Absence of an explicit test means a future refactor can introduce the leak silently.

**Fix shape.** No schema changes. Add `tests/cross_tenant_isolation.test.ts`:

```typescript
describe('Cross-tenant isolation', () => {
  let workspaceA: TestWorkspace, workspaceB: TestWorkspace

  beforeAll(async () => {
    workspaceA = await createTestWorkspace()
    workspaceB = await createTestWorkspace()
    // Seed evidence in workspaceA only
    await seedEvidence(workspaceA.id, 3)
  })

  const endpoints = [
    { method: 'GET',  path: (id: string) => `/cases?workspace_id=${id}` },
    { method: 'GET',  path: (id: string) => `/binders?workspace_id=${id}` },
    { method: 'POST', path: (id: string) => `/cases/${workspaceA.caseId}/evidence`,
      body: { workspace_id: id } },
    { method: 'GET',  path: (id: string) => `/forensic-audit?workspace_id=${id}` },
  ]

  for (const ep of endpoints) {
    it(`${ep.method} ${ep.path(':id')} — workspaceB token cannot access workspaceA data`, async () => {
      const res = await request(app)
        [ep.method.toLowerCase()](ep.path(workspaceA.id))
        .set('Authorization', `Bearer ${workspaceB.token}`)
        .send(ep.body)
      expect([403, 404]).toContain(res.status)
      expect(res.body).not.toMatchObject({ workspace_id: workspaceA.id })
    })
  }
})
```

**Test plan.**
- All read endpoints: workspaceB token returns 404 or empty set for workspaceA IDs.
- All mutation endpoints: workspaceB token returns 403 for workspaceA resources.
- Test runs in CI as part of the standard Vitest suite.

**Owner:** Kasra | **Effort:** 0.5 day | **Blockers:** None.

---

## 2F. T4/T4A Payroll Adapter

**Problem.** `engine/reconciliation.ts` maps evidence to `time_allocations`, not T4/T4A payroll records. CRA requires that the SR&ED salary basis cross-references gross wages from T4. No payroll adapter exists — `adapters/registry.ts` marks it as planned.

**Fix shape.** Add `adapters/payroll-adapter.ts` interface:

```typescript
export interface PayrollAdapter {
  name: 'wagepoint' | 'adp' | 'ceridian'
  fetchT4Records(workspaceId: string, year: number): Promise<T4Record[]>
}

export interface T4Record {
  worker_id:           string
  year:                number
  gross_wages:         number
  sred_eligible_amount: number // operator-supplied or derived
  source_ref:          string  // payroll system record ID
}
```

Implement stubs for Wagepoint, ADP, and Ceridian (top 3 Canadian SMB payroll systems). Each requires OAuth consent identical to the existing QBO pattern.

Migration:

```sql
-- Migration: 0066_payroll_records.sql
CREATE TABLE payroll_records (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID NOT NULL REFERENCES workspaces(id),
  worker_id            UUID NOT NULL REFERENCES user_accounts(id),
  year                 INTEGER NOT NULL,
  gross_wages          NUMERIC(12, 2) NOT NULL,
  sred_eligible_amount NUMERIC(12, 2),
  source_ref           TEXT,
  ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, worker_id, year)
);
```

Update `engine/reconciliation.ts` to cross-reference T4 gross wages against computed SR&ED salary basis. Discrepancies flagged as `payroll_mismatch` warnings on the dossier, surfaced to the practitioner review step.

**Test plan.**
- T4 mismatch between computed SR&ED salary basis and `payroll_records.gross_wages` produces a `payroll_mismatch` warning on the dossier.
- Reconciliation report surfaces discrepancy with delta amount to practitioner.
- Consent gate blocks T4 ingest without `retention_consent = true`.

**Owner:** Kasra | **Effort:** 2 days (adapter stubs + reconciliation update) | **Blockers:** Payroll provider OAuth credentials (Hadi to obtain). Can run in parallel after 2A–2D ship.

---

## 2G. Right-to-Delete + CRA Retention Interaction (Legal Flag, Code Prep)

**Problem.** `routes/privacy.ts` deletes `user_accounts` and relies on cascade for children. It is unclear whether `evidence_items`, `forensic_audit_logs`, and `audit_binders` cascade-delete. CRA can request a binder for 6 years post-filing under the Income Tax Act. If a client invokes PIPEDA right-to-erasure after filing, the cascade may destroy the binder GAF is legally obligated to retain. PIPEDA and CRA retention obligations are in direct tension — this requires Canadian privacy counsel to draw the line.

**Code fix (do not ship until legal review completes).**

Remove cascade deletes on SR&ED evidence tables. Replace with soft-archive with PII scrub:

```sql
-- Migration: 0067_soft_archive_evidence.sql  [BLOCKED — legal review required]
ALTER TABLE evidence_items
  ADD COLUMN deleted_at TIMESTAMPTZ,
  ADD COLUMN pii_scrubbed_at TIMESTAMPTZ;

ALTER TABLE audit_binders
  ADD COLUMN deleted_at TIMESTAMPTZ;

ALTER TABLE forensic_audit_logs
  ADD COLUMN deleted_at TIMESTAMPTZ;

-- Remove CASCADE from evidence_items, audit_binders, forensic_audit_logs
-- foreign keys referencing user_accounts — replace with RESTRICT
ALTER TABLE evidence_items
  DROP CONSTRAINT evidence_items_user_id_fkey,
  ADD CONSTRAINT evidence_items_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES user_accounts(id) ON DELETE RESTRICT;
```

The deletion job in `routes/privacy.ts` scrubs PII fields (name, email, personal identifiers) from evidence records and sets `deleted_at`, but retains the cryptographic content (hashes, Merkle root, timestamps) for 6 years from `locked_at`. Access to archived records requires admin role plus a documented access reason logged to `audit_log`.

**Test plan.**
- User deletion archives (does not hard-delete) binder; `deleted_at` is set.
- PII fields (author name, email) are nulled; `evidence_hash` and `merkle_hash` remain intact.
- Re-computing Merkle root on archived binder returns the same root hash (cryptographic integrity preserved).
- Access to archived record without admin role + access reason returns 403.

**Owner:** Kasra (code prep) | **Effort:** 1 day code | **Blockers:** BLOCKED — Canadian privacy counsel review required before migration ships. Do not start code until legal sign-off. Queue migration in branch, do not merge.

---

## 2H. Ship Order

**Recommended sequence with reasoning:**

**Days 1–3 (Kasra, parallel track A):** `2D → 2B → 2E → 2A`

Ship in this order:

1. **2D first** (0.5 day) — forensic chain is a pure additive column + logic change. No dependencies. Unblocks future forensic verification endpoint.
2. **2B** (1 day) — `source_timestamp` migration. Backfill is safe; no lockouts. Fixes the fabricated-timestamp problem before any new evidence enters the system.
3. **2E** (0.5 day) — cross-tenant test. Zero schema changes. Ships as a test artifact immediately after 2B.
4. **2A** (1 day) — submit route + Merkle root write. Depends on 2C's `practitioner_signoffs` table existing (migration can land without the partnership being operational). The submit route checks for the row at runtime — so the gate is live but won't fire until a practitioner is enrolled.

**Days 1–3 (Hadi, parallel track B):** `2C partnership`

Initiate Boast/Leyton/CPA outreach. This is on the critical path for any real filing. The code for 2C (removing AI auto-population, adding structured form) takes 1 day and can be built while the partnership negotiation runs. The submit gate in 2A will enforce the signoff at runtime once a practitioner exists.

**Days 4–5:** `2F`

Payroll adapter can run after the core chain is solid. It requires payroll OAuth credentials from Hadi (Wagepoint / ADP API keys) and is a self-contained adapter addition. No dependency on 2A–2E.

**Hold:** `2G`

Do not start code or migration for right-to-delete until Canadian privacy counsel delivers an opinion. Queue the migration branch but do not open a PR. Legal sign-off is the only unblock. Estimated wait: 1–3 weeks depending on counsel availability.

**Summary table:**

| Fix | Track | Day | Depends on | Unblocks |
|-----|-------|-----|-----------|---------|
| 2D — forensic chain | Kasra | 1 | — | 2E, 2A |
| 2B — source timestamp | Kasra | 1–2 | — | 2A pre-flight |
| 2E — cross-tenant test | Kasra | 2 | 2B | CI green |
| 2C partnership | Hadi | 1–3 | — | 2A submit gate fires |
| 2C code | Kasra | 2–3 | — | 2A |
| 2A — Merkle root + submit | Kasra | 3 | 2D, 2B, 2C schema | onboarding gate lifted |
| 2F — payroll adapter | Kasra | 4–5 | Hadi OAuth creds | reconciliation complete |
| 2G — right-to-delete | Blocked | — | Legal sign-off | PIPEDA defensibility |

**Gate:** next customer onboarding is blocked until 2A ships (which requires 2B, 2D, and 2C schema). The estimated runway is 3 days of focused Kasra time in parallel with Hadi's partnership call.
