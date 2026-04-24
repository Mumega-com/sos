# Customer Intake → Knight Spawn
**Date:** 2026-04-24 | **Author:** Kasra | **Gate:** Athena | **PM:** Loom | **Status:** Draft v2

---

## What This Is

Squad Service API surface: accept customer intake, hold for Loom review, trigger `mint-knight.py` on approval, seed initial named roles (Inkwell Hive RBAC Primitive 1) at mint time. Data + trigger only — no UI in v1.

---

## Data Model — Migration 0011

```sql
CREATE TABLE customer_intakes (
    id                  TEXT PRIMARY KEY,
    customer_name       TEXT NOT NULL,
    customer_slug       TEXT NOT NULL UNIQUE,  -- becomes project_id
    domain              TEXT,
    repo_url            TEXT,
    icp                 TEXT,
    okrs_json           TEXT DEFAULT '[]',
    cause_draft         TEXT,                  -- editable pre-mint; required at mint
    descriptor_draft    TEXT,                  -- QNFT descriptor
    initial_roles_json  TEXT DEFAULT '["advisor","intern"]',
    -- validated: must be JSON array of strings, max 10 items, each ≤40 chars
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|minted|rejected|failed
    source              TEXT DEFAULT 'direct', -- 'direct'|'ghl'|'api'
    ghl_contact_id      TEXT,
    created_at          TEXT NOT NULL,
    approved_by         TEXT,                  -- agent_id of approver (system/governance token)
    approved_at         TEXT,
    minted_at           TEXT,
    mint_error          TEXT,                  -- populated on status=failed
    knight_name         TEXT
);
```

`failed` status: set when `mint-knight.py` exits non-zero. `mint_error` captures stderr. `approved` is the retry state — re-POST `/customers/{id}/mint` to retry.

---

## Routes

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/customers/intake` | system bearer | Create intake (status=pending) |
| `GET` | `/customers/{id}` | system bearer | Get intake |
| `GET` | `/customers` | system bearer | List (filterable by status) |
| `PATCH` | `/customers/{id}` | system bearer | Edit cause/descriptor/roles (pre-approval only) |
| `POST` | `/customers/{id}/approve` | **system bearer only** | Approve; records approver agent |
| `POST` | `/customers/{id}/reject` | **system bearer only** | Reject |
| `POST` | `/customers/{id}/mint` | system bearer | Trigger mint + seed roles |
| `POST` | `/customers/{id}/seed-roles` | system bearer | Retry role seeding if mint succeeded but seeding failed |
| `POST` | `/webhooks/ghl/lead` | GHL shared secret (`X-GHL-Secret`) | GHL inbound → intake |

`approve` and `reject` are **system bearer only** — no project-owner token. The entity being approved cannot grant its own approval. Loom's COORDINATOR system token is the only valid caller.

---

## Mint Sequence

`POST /customers/{id}/mint`:

1. Gate: status must be `approved`; `cause_draft` must be non-empty → 422 if empty, 409 if wrong status.
2. Write `cause_draft` to a `NamedTemporaryFile` (deleted on exit). Invoke:
   ```
   subprocess.run(
       [sys.executable, MINT_KNIGHT_PATH,
        "--knight-name", knight_name,
        "--customer-slug", slug,
        "--customer-name", customer_name,
        "--cause-file", tmp_cause_path,
        ...],
       shell=False,   # required — never shell=True
       capture_output=True, text=True
   )
   ```
   No user-supplied input in any argument position. `cause_draft` enters only via `--cause-file` (temp file path). `shell=False` is required, not optional.
3. On non-zero exit: set `status=failed`, `mint_error=stderr[:2000]`. Return 500 with `{error: "mint_failed", detail: mint_error}`.
4. On success: set `status=minted`, `knight_name`, `minted_at`.
5. **Seed roles** (Primitive 1): for each name in `initial_roles_json`, call `POST /projects/{slug}/roles`. Default permissions: `inkwell:read:role` for all; `inkwell:write:project` for `advisor` only. Best-effort — mint stays `minted` even if seeding fails (retry via `POST /customers/{id}/seed-roles`).
6. Return `{knight_name, token_prefix, workspace, roles_seeded}`.

---

## Validation

`initial_roles_json` validated at `POST /customers/intake` and `PATCH /customers/{id}`:
- Must parse as valid JSON array of strings.
- Max 10 items; each item ≤ 40 chars, matches `^[a-z0-9-]+$`.
- Return 422 with `{error: "invalid_initial_roles", detail: "..."}` on failure.

---

## GHL Webhook

`POST /webhooks/ghl/lead` verified by `X-GHL-Secret` header (env var `GHL_WEBHOOK_SECRET`). Maps `contact_id`, `company`, `domain`, `tags`, `custom_fields.icp/okrs` to intake row. `source=ghl`, status=pending. No auto-approval.

---

## Migration Note

Inkwell Hive RBAC planned roles at 0010 — taken by `project_resources` (2026-04-24). Revised: intake=0011, roles tables=0012, squad KB columns=0013.

---

## Tests

1. Create intake → status=pending.
2. Mint on pending → 409.
3. Approve → mint → status=minted, knight workspace exists, roles seeded.
4. Mint with empty `cause_draft` → 422.
5. Mint failure (bad slug) → status=failed, mint_error populated, retry → succeeds.
6. PATCH pre-approval → fields updated. PATCH post-approval → 409.
7. Approve with project owner token → 403 (system bearer only).
8. Approve → mint → mint again → 409 (already minted).
9. GHL webhook valid secret → intake row, `source=ghl`.
10. GHL webhook bad secret → 401.
11. `initial_roles_json` invalid JSON → 422 at create time.
12. `GET /projects/{slug}/roles` after mint → advisor + intern present.
13. `POST /customers/{id}/seed-roles` when status=minted but roles absent → seeds and returns roles_seeded count.
