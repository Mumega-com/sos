# `tokens.json` → capability-signed assertions

**Date:** 2026-04-18
**Status:** Plan. No code in this commit.
**Nests under:** `SECURITY_MODEL.md` (trust model) + `docs/plans/2026-04-18-coherence-plus-us-market.md` (island #9).

---

## What exists today (the honest state)

`sos/bus/tokens.json` is a flat JSON array of records. Each looks like:

```json
{
  "label": "Hadi — Claude.ai MCP v2",
  "token": "sk-...",            // deprecated: raw value (pre-SEC-001)
  "token_hash": "abc...",       // sha256 hex of the raw token
  "hash": "$2b$...",            // optional bcrypt alternative
  "agent": "hadi",
  "project": null,               // null = system/admin scope
  "active": true
}
```

Verification is by SHA-256 hash match (or bcrypt for older entries) via `sos.services.auth.verify_bearer`. One canonical read path since the auth consolidation (island #3 yesterday). Good enough for today's bearer-token model.

**What `tokens.json` doesn't do:**
- No signature — possession of the bearer string is equivalent to authorization
- No capability separation — an admin token can do anything; a tenant token can do anything within its tenant
- No revocation proof — revoking a token requires editing `tokens.json` server-side, no cryptographic way to prove revocation to a third party
- No offline verification — every request requires reading the server-side hash list
- No delegated signing — an agent can't issue a sub-capability to another agent

---

## What the kernel already specifies (the target)

From `sos/kernel/identity.py`:

```python
class Identity:
    id: str                     # "agent:hadi", "user:hossein", etc.
    public_key: Optional[str]   # Ed25519 — already there, currently unused
    fingerprint -> str          # sha256[:8] of public_key (already implemented)
    verification_status: VerificationStatus  # UNVERIFIED/PENDING/VERIFIED/REVOKED
    verified_by: Optional[str]  # which authority verified this identity
```

Every Identity already has a slot for an Ed25519 public key. River (the root gatekeeper, per the identity module) is specified as the authority that verifies identities. Nothing is using these fields yet.

Per `SECURITY_MODEL.md`, the target trust model is **capability-based**: a caller doesn't carry a bearer token; they carry a signed capability assertion that names the bearer, the capabilities granted, an expiry, and a signature from the granting authority.

---

## The plan in three phases

### Phase 1 — Dual-write (additive, non-breaking)

Every token minted by `scripts/sos-provision-agent-identity.py` (and by `sos/services/saas/signup.py`) starts writing two things:

1. **The existing bearer** (for backward compat) — keeps `verify_bearer` path working
2. **A capability assertion** — a signed record of "River authority grants `agent:<slug>` the capabilities [read, write, publish, witness]" with a signature, expiry, and revocation epoch

Assertion shape (first draft — not final):

```json
{
  "assertion_version": "1",
  "issuer": "agent:river",
  "subject": "agent:hadi",
  "capabilities": ["bus.read", "bus.write", "marketplace.publish", "witness.cast"],
  "tenant_scope": null,              // null = system; otherwise the tenant slug
  "issued_at": "2026-04-18T14:00:00Z",
  "expires_at": "2027-04-18T14:00:00Z",
  "revocation_epoch": 0,              // bumped by issuer on revocation
  "signature": "ed25519:...",         // over the canonical JSON, using issuer's private key
  "signature_alg": "ed25519"
}
```

New file: `sos/kernel/capability.py` (module stub already exists — extend it).

**Impact during Phase 1:** zero. Every caller still uses `verify_bearer`. Assertions sit alongside, unused.

### Phase 2 — Verify alongside (trust but measure)

`sos.services.auth.verify_bearer` starts additionally checking whether the bearer has a matching capability assertion. If both match → accept. If only bearer matches → accept but log a "no-capability-assertion" warning. If only capability matches → accept (future behavior).

This is the measurement phase. We see how many tokens have assertions vs not. We build tools to backfill assertions for tokens that predate them.

### Phase 3 — Capability-first (break change, ratcheted)

After Phase 2 shows 100% of live tokens have assertions + a migration deadline passes:

- `verify_bearer` rejects bearers without a matching assertion
- Assertions become mandatory for all new tokens
- Clients can carry **just the assertion** (no bearer string) — the signature IS the authentication
- Offline verification becomes possible: any service with River's public key can verify an assertion locally without calling the auth service

---

## Specific capabilities (first cut)

| Capability | What it grants |
|---|---|
| `bus.read` | Read own messages + public channels |
| `bus.write` | Publish v1 messages (with source pattern check) |
| `bus.broadcast` | Publish to `sos:channel:global` |
| `marketplace.read` | Read SkillCards, Artifacts |
| `marketplace.publish` | Author new SkillCards + mint Artifacts |
| `marketplace.purchase` | Debit wallet to invoke a SkillCard |
| `witness.cast` | Submit witness events (vote + latency → omega, ΔC) |
| `squad.join` | Be added as a member to a Guild |
| `squad.manage` | Add/remove Guild members (Guild leaders) |
| `economy.debit` | Deduct from own wallet |
| `economy.credit` | Be credited from ledger (no special grant; implicit for all identities) |
| `economy.transmute` | Trigger $MIND → SOL bridge (gated; requires witness council) |
| `admin.full` | Every above + tenant-scope override + verification.status edits |

Each capability is a string in the `capabilities: []` array of an assertion. `admin.full` is a superset and is only issued to system identities by River.

---

## River as root authority

Today `RIVER_IDENTITY` (in `sos/kernel/identity.py::RIVER_IDENTITY`) is a named singleton with `metadata.role = "root_gatekeeper"`. No private key is actually materialized anywhere.

Phase 1 generates River's Ed25519 keypair the first time capability signing is attempted. Private key stored at `${SOS_HOME}/keys/river.priv` (mode 0600). Public key published at `${SOS_HOME}/keys/river.pub` and also propagated to:
- The kernel as a compiled constant (for offline verification)
- `app.mumega.com/.well-known/river-key.pub` (for third-party verifiers — sovereign node operators, $MIND redemption bridge, etc.)

**Rule:** the River private key NEVER leaves the primary junction VPS. For sovereign nodes and forks, the local node generates its own `node:<slug>` keypair and signs its own local-scope assertions. Assertions with scope beyond the local node require River's co-signature (round-trip to the junction).

---

## Interaction with Guild identities

A Guild (squad in identity-hierarchy terms) can be granted assertions too:

- `guild:thresh-backend` identity has its own public key
- Members (agents + humans) inherit Guild capabilities transitively, bounded by the per-member assertion the Guild leader signs
- When a Guild agent publishes a SkillCard, the assertion chain is `river → guild:thresh-backend → agent:sam-claude-code`. Verifiers walk the chain.

This is exactly how the Witness Protocol needs identity to work at scale: witnesses can be members of witnessing Guilds that carry reputation as a collective, and revenue splits flow through the Guild ledger before reaching individual wallets.

---

## What this commit doesn't ship

- Capability signing implementation (Phase 1 code) — deferred until after Stage 2 Brain build
- Migration tool for existing `tokens.json` entries — deferred
- River keypair generation — deferred
- `verify_bearer` changes — deferred
- `sos/kernel/capability.py` expansion — deferred

What it does ship is the **plan**. The existing `tokens.json` flow stays canonical. This doc becomes the reference when the work is picked up in a later island (tentatively mid-v0.4.5 or v0.5.0 pre-work).

---

## Cross-references

- `SECURITY_MODEL.md` — trust tier framework this plan implements
- `sos/kernel/identity.py` — `Identity.public_key`, `VerificationStatus`
- `sos/services/auth/__init__.py` — current `verify_bearer`
- `sos/bus/tokens.json` — current storage (will become the bearer-compat side of dual-write during Phase 1)
- `docs/plans/2026-04-18-coherence-plus-us-market.md` — island #9 (this plan's origin)
