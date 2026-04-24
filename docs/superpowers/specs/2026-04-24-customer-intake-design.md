# Customer Intake → Knight Spawn
**Date:** 2026-04-24 | **Author:** Kasra | **Gate:** Athena | **PM:** Loom | **Status:** Draft v1

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
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|minted|rejected
    source              TEXT DEFAULT 'direct', -- 'direct'|'ghl'|'api'
    ghl_contact_id      TEXT,
    created_at          TEXT NOT NULL,
    approved_by         TEXT,
    approved_at         TEXT,
    minted_at           TEXT,
    knight_name         TEXT
);
```

---

## Routes

Auth: system bearer or project `role=owner`.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/customers/intake` | Create intake (status=pending) |
| `GET` | `/customers/{id}` | Get intake |
| `GET` | `/customers` | List (filterable by status) |
| `PATCH` | `/customers/{id}` | Edit cause/descriptor/roles pre-approval |
| `POST` | `/customers/{id}/approve` | Loom approves |
| `POST` | `/customers/{id}/mint` | Trigger mint + seed roles |
| `POST` | `/customers/{id}/reject` | Reject |
| `POST` | `/webhooks/ghl/lead` | GHL inbound → intake (secret-verified) |

---

## Mint Sequence

`POST /customers/{id}/mint`:

1. Gate: status must be `approved`; `cause_draft` must be non-empty.
2. Write cause to temp file, invoke `mint-knight.py` via subprocess.
3. On success: set `status=minted`, `knight_name`, `minted_at`.
4. **Seed roles** (Primitive 1): for each name in `initial_roles_json`, call `POST /projects/{slug}/roles`. Default permissions: `inkwell:read:role` for all seeded roles; `inkwell:write:project` for `advisor` only. Best-effort — mint succeeds even if role seeding fails (retry via `POST /customers/{id}/seed-roles`).
5. Return `{knight_name, token_prefix, workspace, roles_seeded}`.

---

## GHL Webhook

`POST /webhooks/ghl/lead` verified by `X-GHL-Secret` header. Maps `contact_id`, `company`, `domain`, `tags`, `custom_fields.icp/okrs` to intake row. `source=ghl`, status=pending. No auto-approval.

---

## Migration Note

Inkwell Hive RBAC planned roles at 0010 — that slot is taken (`project_resources`, 2026-04-24). Revised sequence: intake=0011, roles tables=0012, squad KB columns=0013.

---

## Tests

1. Create intake → status=pending.
2. Mint on pending → 409.
3. Approve → mint → status=minted, knight workspace exists, roles seeded.
4. Mint with empty `cause_draft` → 422.
5. GHL webhook valid secret → intake row with `source=ghl`.
6. GHL webhook bad secret → 401.
7. `GET /projects/{slug}/roles` after mint → advisor + intern present.
