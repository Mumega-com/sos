# Mesh Security Wave — v0.9.2.1

**Status:** planned 2026-04-19
**Blocks:** comms-fix sequence (ACK → /handoff → role-resolving lookup), Phase 4 (Mumega-edge)
**Follows:** v0.9.2 (Phase 3 — mesh enrollment)

## Why

v0.9.2 shipped the mesh as *plumbing*: agents enroll, squads resolve, pruner
evicts the dead. But the mesh is not *trustworthy* yet — anyone with
`sk-sos-system` can enroll `agent:anything` because `/mesh/enroll` never
challenges the caller's identity. `AgentCard.resolve_identity()` is a stub
(`NotImplementedError`). `AgentIdentity.public_key` is stored but never
verified. Messages are unsigned.

Before we expose any of this through Phase 4 (Mumega-edge as public API),
the soul layer has to go from "modeled" to "enforced."

## Scope — three gaps, one sprint

1. **Signed enrollment** — every `/mesh/enroll` requires an Ed25519 signature
   over a server-issued nonce. First enrollment uses Trust-On-First-Use
   (TOFU): accept + store the submitted `public_key` on the AgentIdentity.
   Subsequent enrolls must match, or 409.
2. **First-seen alerting** — TOFU enrollments emit `agent.enrolled.first_seen`
   on the bus. Dashboard badges first-seen agents for 24h. Optional Discord
   webhook when configured.
3. **Per-service system tokens** — split `sk-sos-system` into
   `SOS_REGISTRY_TOKEN`, `SOS_SAAS_TOKEN`, `SOS_SQUAD_TOKEN`,
   `SOS_BILLING_TOKEN`. Each service runs with its own token. The root
   `sk-sos-system` stays as break-glass admin.

Out of scope (deferred to later security waves):
- TLS between services (move when registry leaves the box)
- Bus message signing (separate wave; enrollment auth is the foundation)
- Token rotation automation
- Anomaly detection / behavior analytics
- Network ACLs / firewall rules

## Steps

### S1 — Ed25519 crypto module

**File:** `sos/kernel/crypto.py` (new)
**Change:** Pure functions wrapping PyNaCl signing.

```python
def generate_keypair() -> tuple[str, str]: ...  # (priv_b64, pub_b64)
def sign(priv_b64: str, message: bytes) -> str: ...  # sig_b64
def verify(pub_b64: str, message: bytes, sig_b64: str) -> bool: ...
```

PyNaCl is already in `[project].dependencies`. No new deps.

**Tests:** `sos/tests/test_crypto.py` — roundtrip, wrong key, malformed input.

**Outcome:** `pytest sos/tests/test_crypto.py` passes.

### S2 — Nonce store + /mesh/challenge

**File:** `sos/services/registry/nonce_store.py` (new)
**Change:** Redis-backed single-use nonces.

```python
def issue(agent_id: str, ttl_s: int = 60) -> tuple[str, int]: ...  # (nonce, expires_at)
def consume(agent_id: str, nonce: str) -> bool: ...  # True if valid+deleted
```

Key format: `sos:mesh:nonce:{agent_id}:{nonce}` with 60s TTL. `consume`
uses Redis `GETDEL` (atomic) — a replayed nonce fails.

**File:** `sos/services/registry/app.py`
**Change:** Add `POST /mesh/challenge`.

```python
@app.post("/mesh/challenge")
async def mesh_challenge(body: {agent_id}, authorization) -> {nonce, expires_at}:
    # No bearer required — nonce alone is useless without the matching private key.
    # Rate-limited via existing per-tenant limiter.
```

**Outcome:** `curl POST /mesh/challenge {agent_id:"agent:hermes"}` returns
a 24-byte base64 nonce with `expires_at` 60s in the future.

### S3 — Signed /mesh/enroll + TOFU

**File:** `sos/services/registry/app.py`
**Change:** Extend `MeshEnrollRequest`:

```python
class MeshEnrollRequest(BaseModel):
    # existing fields ...
    public_key: str            # base64 Ed25519 public key
    nonce: str                 # from /mesh/challenge
    signature: str             # sign(priv, f"{agent_id}|{nonce}|{canonical_payload_hash}")
```

Enroll flow:
1. `nonce_store.consume(agent_id, nonce)` — reject if unknown/expired/replayed
2. Verify signature over `f"{agent_id}|{nonce}|{sha256(canonical body)}"`
3. Look up stored `AgentIdentity` via `registry.read_one(agent_id, project)`
   - **None** → TOFU: write AgentIdentity with submitted `public_key`,
     `verification_status=VERIFIED`, flag as first-seen
   - **Exists & matches** → proceed with card upsert (happy path)
   - **Exists & mismatch** → 409 Conflict, log SECURITY event on audit stream
4. Upsert the AgentCard as today

**File:** `sos/contracts/agent_card.py`
**Change:** Implement `AgentCard.resolve_identity()` — remove the
`NotImplementedError` stub; call `sos.services.registry.read_one()`.

**Outcome:** Re-enrolling hermes with a stored public_key mismatch returns 409.

### S4 — First-seen alert

**File:** `sos/services/registry/app.py`
**Change:** On TOFU path, emit bus event:

```python
await bus.publish(
    subject="agent.enrolled.first_seen",
    payload={"agent_id": body.agent_id, "project": effective_project,
             "public_key_fp": sha256(public_key)[:8], "enrolled_at": now},
)
```

**File:** `sos/services/dashboard/routes/bus.py` (or equivalent mesh route)
**Change:** Query AgentCards where `(now - registered_at) < 24h AND identity_created_at == registered_at` → render NEW badge.

**File:** `sos/services/registry/app.py` (optional)
**Change:** If `os.environ.get("SOS_ALERT_WEBHOOK")` is set, POST to it.

**Outcome:** Enrolling a brand-new `agent:testfoo` emits the bus event
visible via `redis-cli XRANGE sos:bus:...`; dashboard shows NEW badge.

### S5 — Per-service system tokens

**File:** `sos/kernel/auth.py`
**Change:** Extend `_ENV_TOKENS` tuple:

```python
_ENV_TOKENS = (
    ("SOS_SYSTEM_TOKEN", True, "*"),               # break-glass admin
    ("SOS_REGISTRY_TOKEN", True, "registry:*"),     # registry-only
    ("SOS_SAAS_TOKEN", True, "saas:*"),
    ("SOS_SQUAD_TOKEN", True, "squad:*"),
    ("SOS_BILLING_TOKEN", True, "billing:*"),
    ("MIRROR_TOKEN", True, "*"),
    ("BUS_BRIDGE_TOKEN", True, "bus:*"),
    ("CYRUS_BUS_TOKEN", False, "bus:send"),
)
```

`AuthContext` gains a `scopes: list[str]` field. Services that want scope
enforcement call a helper `require_scope(ctx, "registry:mesh_enroll")`.

**File:** `systemd/sos-registry.service`
**Change:** Use `SOS_REGISTRY_TOKEN` instead of `SOS_SYSTEM_TOKEN`.

**File:** `~/.env.secrets` (per-host)
**Change:** Generate four new tokens, document in docs/runbook/secrets.md.

**Outcome:** Services run with narrow tokens. Host compromise of the saas
server can't enroll agents.

### S6 — Contract + integration tests

**File:** `tests/contracts/test_mesh_enroll_signed.py` (new)
- 401 on missing signature
- 403 on bad signature / wrong key
- 403 on replayed nonce
- 200 on TOFU first-enroll; subsequent enroll verifies stored pub_key
- 409 on pub_key rotation attempt
- 403 on scoped token trying cross-service action

**File:** `tests/test_mesh_enroll_e2e.py` (new)
- Full round-trip: `/mesh/challenge` → sign(nonce) → `/mesh/enroll` → assert
  AgentIdentity + AgentCard written with matching public_key

**Outcome:** `pytest tests/contracts/test_mesh_enroll_signed.py tests/test_mesh_enroll_e2e.py` green.

### S7 — Ship v0.9.2.1

**Files:** `CHANGELOG.md`, `pyproject.toml`, `sos/__init__.py` (version)
**Change:** Add v0.9.2.1 section; bump version 0.9.2 → 0.9.2.1.

Commit:
```
security(mesh): v0.9.2.1 — signed enroll + TOFU + per-service tokens

Closes the three v0.9.2 auth gaps: /mesh/enroll now requires an
Ed25519 signature over a server-issued nonce; first enrollments
use Trust-On-First-Use + emit agent.enrolled.first_seen;
sk-sos-system split into per-service narrow-scope tokens.
```

Tag `v0.9.2.1`. Restart registry service. Re-enroll hermes with the new
flow to verify end-to-end.

**Outcome:** `git tag` shows v0.9.2.1; `curl /health` + `curl /mesh/squad/mesh` still green; hermes visible with matching stored public_key.

## Non-goals / explicit deferrals

- Message signing on the bus (separate wave)
- TLS on service-to-service HTTP (separate wave, gated on services leaving localhost)
- Token rotation / revocation automation
- Anomaly detection on enroll patterns
- HSM / hardware-backed keys

## Risk + rollback

- **Risk:** every agent bootloader needs updated to sign its enroll. `sos/agents/join.py`, `sos/cli/init.py`, any agent using `RegistryClient.mesh_enroll()`.
  - **Mitigation:** one-commit sweep updating all enrolling clients. Contract test enforces sign-every-enroll.
- **Rollback:** revert the v0.9.2.1 tag, restart registry on v0.9.2 binary.
  Redis state carries AgentIdentity rows with `public_key` set — no data
  loss; v0.9.2 simply ignores the field.
