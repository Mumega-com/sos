# Burst 2B-2 — Unified Hash-Chained Audit Stream + WORM Anchor

**Author:** Loom
**Date:** 2026-04-24
**Phase:** Sprint 002 — Burst 2B hardening (evidence + non-repudiation)
**Depends on:** Kernel bus, Mirror service, Squad service, DISP-001, plugin.yaml contract (§6)
**Gate:** Athena
**Owner:** Kasra
**Effort:** ~8 days

---

## 1. Goal

One tamper-evident audit stream across the entire system. Every write that matters — kernel auth decision, Mirror engram mutation, Squad task transition, Dispatcher session mint, plugin side effect — emits an `audit_events` record. Records are SHA-256 chained per stream so any reordering or deletion is detectable. A scheduled job anchors chain heads to Cloudflare R2 with object-lock (WORM), giving us an offline proof that no one — including the database admin — has rewritten history.

This is the evidence substrate for ISO 42001, SOC 2, and any customer asking "prove nothing was changed." It also backs incident forensics.

## 2. Schema

Single append-only table, partitioned by `stream_id`:

```
-- One PostgreSQL sequence per stream_id; allocated lazily on first emit.
-- Per Athena G4: app-side counters race under concurrency. nextval is atomic.
-- Sequence naming: audit_seq_<stream_id>  (e.g. audit_seq_kernel, audit_seq_mirror)

audit_events (
  id           UUID PRIMARY KEY,
  stream_id    TEXT NOT NULL,         -- e.g. 'kernel', 'mirror', 'squad', 'dispatcher', 'plugin:<name>'
  seq          BIGINT NOT NULL,       -- nextval('audit_seq_<stream_id>') — never an app-side counter
  ts           TIMESTAMPTZ NOT NULL,
  actor_id     TEXT NOT NULL,         -- principal or 'system'
  actor_type   TEXT NOT NULL,         -- 'agent' | 'human' | 'system'
  action       TEXT NOT NULL,         -- verb: 'created', 'updated', 'granted', 'denied', ...
  resource     TEXT NOT NULL,         -- e.g. 'role_assignment:abc', 'engram:xyz'
  payload      JSONB,                 -- ≤8KB at emission; emit_audit trims with redaction flag if larger
  payload_redacted BOOLEAN DEFAULT false, -- true when emit_audit trimmed for size
  prev_hash    BYTEA,                 -- hash of previous event in this stream
  hash         BYTEA NOT NULL,        -- SHA-256(prev_hash || canonical_json(event))
  signature    BYTEA,                 -- Ed25519 — MANDATORY on stream_id='dispatcher' (crosses trust boundary), optional elsewhere
  UNIQUE(stream_id, seq)
)
```

One genesis event per stream with `prev_hash = NULL`. `hash` is computed over a canonical JSON serialization of the event minus the `hash` field itself.

## 3. Hash Algorithm

- Canonical JSON: sorted keys, UTF-8, no whitespace.
- `hash = SHA-256(prev_hash_bytes || canonical_bytes)`.
- Per-stream chains (not one global chain) so high-throughput streams don't serialize through a global lock.
- Optional Ed25519 signatures on hash — used for streams that cross a trust boundary (e.g. dispatcher events that will be re-consumed by Mirror).

## 4. WORM Anchor

**Choice: Cloudflare R2 with Object Lock** (cost and alignment with the rest of the stack). S3 Glacier is viable but adds a second cloud provider for one feature.

- Every 15 minutes a job walks each stream's latest chunk, records `(stream_id, last_seq, last_hash, ts)`, and writes an anchor file to an R2 bucket with `Object Lock` in compliance mode, retention 7 years.
- Anchors are named `anchors/{yyyy}/{mm}/{dd}/{stream_id}-{seq}.json` and themselves chained (each anchor references the prior anchor's hash).
- A `verify_chain(stream_id, from_seq, to_seq)` utility recomputes the chain from the DB and checks endpoints against R2 anchors.

## 5. Emission Points

Kernel auth decisions, role/permission changes, Mirror engram create/update/delete, Squad task state transitions, DISP-001 session mint/revoke, plugin lifecycle events, SCIM events (Burst 2B-1), MFA challenges, secret reads (Burst 2B-4). Plugins emit via a shared `emit_audit(event)` helper that enforces schema + redaction.

**`emit_audit` enforces (per Athena G4):**
- `seq = nextval('audit_seq_' || stream_id)` (never app-side counter)
- `len(canonical_json(payload)) ≤ 8192 bytes`; if larger, payload is redacted to `{summary, hash_of_full}` and `payload_redacted = true`
- For `stream_id = 'dispatcher'`, signature is required (Ed25519 over hash with the dispatcher's signing key); helper raises if signing key is unavailable
- For all other streams, signature is optional (set if signing key available; null otherwise)

## 6. Acceptance Criteria

1. **Coverage.** Every kernel + service write produces an `audit_events` entry; grep-for-writes audit against the four services shows zero gaps.
2. **Chain integrity.** `verify_chain` across all streams returns OK after 24h of normal load; a deliberate mutation of one row fails verification at that row.
3. **WORM proof.** Anchor files exist in R2 with object-lock enabled; an attempt to overwrite one is rejected by R2; `verify_chain` against the anchor detects any DB-side tampering.
4. **Perf budget.** p99 write latency overhead < 5ms per emission; anchor job completes < 60s.
