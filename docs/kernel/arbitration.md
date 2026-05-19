# `sos.kernel.arbitration` — Deliberative Arbitration

**Introduced:** v0.5.2 · **Contract:** `sos.contracts.arbitration.ArbitrationDecision` (frozen)

## What this is

The policy gate evaluates each caller in isolation. Two agents calling `can_execute`
independently on the same resource can both receive `allowed=True` because the gate never
sees the other proposal. Arbitration is the cross-proposer layer: it records each agent's
intent into the audit stream, reads across all proposals in a bounded time window, applies
the rank rule, and returns exactly one winner. The gate short-circuits losers before any
bearer, scope, FMAAP, or governance signal runs.

## Architecture

Proposals ARE `AuditEventKind.INTENT` events tagged `metadata.arbitration=True`. The
arbitration module reads those events via `sos.kernel.audit.read_events`, applies the rank
function, and emits one `AuditEventKind.ARBITRATION` event. No new persistence layer. The
audit spine reserved `ARBITRATION` in v0.5.0 — v0.5.2 cashes that receipt.

```
propose_intent(agent, action, resource, ...)
  ─▶  append INTENT event  (existing audit, disk-authoritative, fsync'd)
                │
                ▼
arbitrate(resource, tenant, window_ms)
  ─▶  read_events(kind=INTENT, target==resource, timestamp >= now - window)
  ─▶  sort by (priority → coherence → recency)
  ─▶  append ARBITRATION event
  ─▶  return ArbitrationDecision
```

## Surface

```python
from sos.kernel.arbitration import propose_intent, arbitrate, read_proposals
```

**`propose_intent`** — record a proposal; return proposal id (audit event id).

```python
async def propose_intent(
    *, agent: str, action: str, resource: str,
    tenant: str = "mumega", priority: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str:
```

**`arbitrate`** — pick one winner across proposals in window; emit `ARBITRATION` event.

```python
async def arbitrate(
    *, resource: str, tenant: str = "mumega",
    window_ms: int = 500,
    strategy: str = "priority+coherence+recency",
) -> ArbitrationDecision:
```

**`read_proposals`** — observability helper; returns raw proposals without deciding.

```python
def read_proposals(tenant: str, resource: str, *, window_ms: int = 500) -> list[AuditEvent]:
```

## The rank rule — `priority+coherence+recency`

Applied once, descending, deterministic:

1. **Priority** — `metadata.priority` (int, default `0`). Highest wins outright. The
   claim is recorded in the audit trail.
2. **Coherence** — on tie: `sum(G[agent][skill] for skill in G[agent])` from
   `sos.kernel.conductance`. This is the agent's proven-flow signal — the same matrix
   FMAAP pillar 1 reads. Agents absent from `G` score `0`. Unavailable conductance file
   → all agents score `0`; falls through to recency (fail-soft).
3. **Recency** — on tie: most recent `timestamp` wins. ISO-8601 sorts lexicographically.

The `strategy` string is recorded in `ArbitrationDecision`. Future strategies ship as new
string values without schema churn.

## Contracts

`sos.contracts.arbitration` — two frozen Pydantic v2 models, additive-only since v0.5.2:

```python
class LoserRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    agent: str
    proposal_id: str
    reason: str          # "lost to {winner} (priority=N vs M, conductance=...)"
    priority: int | None = None

class ArbitrationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    resource: str
    tenant: str
    strategy: str        # "priority+coherence+recency" (v0.5.2 baseline)
    window_ms: int
    winner_agent: str | None       # None when no proposals in window
    winner_proposal_id: str | None
    winner_reason: str             # priority + conductance + timestamp of winner
    losers: list[LoserRecord]
    proposal_count: int
    audit_id: str | None           # populated after audit write succeeds
    metadata: dict[str, Any]       # winner_priority, winner_conductance
```

Never remove fields, never narrow types, never rename. `tests/contracts/test_arbitration_schema_stable.py`
snapshots the v0.5.2 baseline and fails any PR that violates these rules. If the test
fails, the answer is almost never "update the snapshot."

## Gate integration

`can_execute` accepts three new keyword arguments in v0.5.2. Default `propose_first=False`
preserves every existing caller unchanged.

```python
decision = await can_execute(
    agent="loom",
    action="content_publish",
    resource="post_42",
    tenant="mumega",
    propose_first=True,    # opt-in; default False
    priority=5,
    window_ms=500,
)
```

When `propose_first=True`:

1. Gate calls `propose_intent(agent, action, resource, tenant, priority)`.
2. Gate calls `arbitrate(resource, tenant, window_ms)`.
3. **Winner** (`winner_proposal_id == caller's proposal_id`) — falls through to normal
   bearer / scope / FMAAP / governance signals.
4. **Loser** — returns `PolicyDecision(allowed=False, tier="denied",
   pillars_failed=["arbitration"])` immediately. No further signals run.

## Durability

The audit spine is the only persistence layer. Reads via `sos.kernel.audit.read_events`;
writes via `sos.kernel.audit.append_event`. Disk is authoritative (`fsync` per write).
Redis bus stream is observational and best-effort — Redis down → bus emit skipped; disk
record already written.

Each arbitration produces exactly one `AuditEventKind.ARBITRATION` event carrying:
`window_ms`, `proposal_count`, `losers` (model-dumped), `winner_priority`,
`winner_conductance`.

## Failure modes

Arbitration failures never raise from the public API:

- `read_proposals` raises → `arbitrate` catches, logs warning, returns no-winner decision.
- Conductance file unavailable → `_agent_conductance_sum` returns `0.0` for all agents;
  ranking falls through to recency.
- `append_event` raises in `_emit` → logs warning, returns decision without `audit_id`.
  An audit hiccup must not be indistinguishable from a denial.
- Unknown `strategy` value → falls back to default strategy; requested value is recorded.

In all failure cases callers using `propose_first=True` receive `allowed=False` with
`pillars_failed=["arbitration"]` — breakage never silently allows contested actions.

## Example

```python
from sos.kernel.arbitration import propose_intent, arbitrate

# Agent Loom proposes
await propose_intent(agent="loom", action="publish", resource="post_42",
                     tenant="mumega", priority=5)

# Agent Athena proposes (lower priority)
await propose_intent(agent="athena", action="publish", resource="post_42",
                     tenant="mumega", priority=0)

# Arbitrate
decision = await arbitrate(resource="post_42", tenant="mumega", window_ms=500)
assert decision.winner_agent == "loom"
assert decision.proposal_count == 2
assert len(decision.losers) == 1
assert decision.audit_id is not None   # ARBITRATION event on disk
```

## What's next

Deferred to v0.5.3+: per-squad dynamic `window_ms` sized by each squad's coherence score;
arbitration replay tooling (`sos.cli arbitration replay`) that reads `ARBITRATION` events
and explains outcomes in human-readable form; migration of the remaining ~10 service-side
permission checks to `can_execute`. None of these reopen the module or the contracts.
