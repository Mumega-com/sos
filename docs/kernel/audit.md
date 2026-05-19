# `sos.kernel.audit` — Unified Audit Stream

**Introduced:** v0.5.0 · **Contract:** `sos.contracts.audit.AuditEvent` (frozen)

## What this is

The kernel's single, append-only record of every governed action. Every
policy decision, every intent, every arbitration outcome eventually
lands here. One schema, one writer, one read API.

## What this is NOT

- Not application logging. Use `logging`/`structlog` for that.
- Not request tracing. Use `sos.observability.tracing` for that.
- Not the bus. The bus stream at `sos:audit:{tenant}` is a real-time
  observational mirror; the disk file is the source of truth.

Audit is for decisions and actions that governance, policy, and
arbitration need to replay or justify. If a line isn't worth showing
to a human asking "why did the system do X," it doesn't belong here.

## Surface

Three public functions, nothing else:

```python
from sos.contracts.audit import AuditEvent, AuditEventKind, AuditDecision
from sos.kernel.audit import append_event, read_events, new_event

# Build an event (id + timestamp auto-filled):
ev = new_event(
    agent="governance",
    tenant="mumega",
    kind=AuditEventKind.INTENT,
    action="content_publish",
    target="post_42",
    decision=AuditDecision.ALLOW,
    reason="act_freely tier",
    policy_tier="act_freely",
)

# Persist it:
event_id = await append_event(ev)

# Read back:
events = read_events("mumega", kind=AuditEventKind.INTENT, limit=50)
```

That is the entire API. There is no class, no config object, no
middleware hook, no registration step. Add a new event kind by adding
a value to `AuditEventKind`, not by adding code here.

## Storage

- **Disk (authoritative)**: `~/.sos/audit/{tenant}/{YYYY-MM-DD}.jsonl`
  - One line per event, `AuditEvent.model_dump_json()` format.
  - Write is `open("a") + write + flush + fsync`. Durable.
  - If disk write fails, `append_event` raises. Losing an audit record
    silently is worse than a governance hiccup.
- **Bus (observational)**: `sos:audit:{tenant}` Redis stream.
  - Best-effort `XADD` with `maxlen=10000` (approximate trim).
  - Redis down → emit is skipped; disk persistence continues normally.
  - Consumers tail this stream for real-time alerting / dashboards.

## Durability contract

The `AuditEvent` schema is **frozen at v0.5.0**. What this means:

- **Never remove fields.** Adds only.
- **Never narrow types.** `int` stays `int`; `str | None` stays
  `str | None` or widens.
- **Never rename fields.** A rename is a remove + add.
- **New enum values are allowed.** `AuditEventKind.POLICY_DECISION`
  lands in v0.5.1 without schema churn.

`tests/contracts/test_audit_schema_stable.py` snapshots the v0.5.0
baseline and fails any PR that breaks these rules. If the test fails,
the correct response is almost never "update the snapshot" — it is
"find a non-breaking way to express the change."

## Who writes to audit today

- `sos.kernel.governance.before_action` — one `INTENT` event per
  governed action, plus a denial event when budget blocks.

## Who writes to audit next

- **v0.5.1 — `sos.kernel.policy`**: every `can_execute()` decision
  emits `AuditEventKind.POLICY_DECISION`.
- **v0.5.2 — `sos.kernel.arbitration`**: every conflict resolution
  emits `AuditEventKind.ARBITRATION`.

Both slot in by writing an `AuditEvent` and calling `append_event`.
No changes to this module are required.

## Why disk-first, bus-second

Audit must survive Redis being down, network partitions, and misconfig.
A kernel that drops audit events when infrastructure wobbles is not
really auditing. Operators can tail the bus for real-time visibility,
but the disk file is the record that survives.

The same pattern lives in `sos.kernel.governance` (legacy intent file)
and `sos.services.economy.usage_log`. Audit is the unified successor.

## Legacy compatibility (v0.5.x only)

`governance.before_action` still writes the legacy per-tenant intent
file at `~/.sos/governance/intents/{tenant}/{date}.jsonl` alongside
the new audit event. This shim will be removed in v0.5.2 once any
external tooling reading that path has migrated to `read_events()`.
