from __future__ import annotations

import argparse
import concurrent.futures
import hmac
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable

import anyio
import bcrypt
import requests
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sos.kernel.capability import Capability, CapabilityAction, verify_capability
from sos.kernel.identity import SYSTEM_IDENTITY, AgentIdentity, Identity, IdentityType, UserIdentity
from sos.services.squad.service import DEFAULT_TENANT_ID, SquadDB, now_iso


SYSTEM_TOKEN = os.getenv("SOS_SYSTEM_TOKEN", "")
security = HTTPBearer(auto_error=False)

OPERATION_MAP: dict[tuple[str, str], CapabilityAction] = {
    ("squads", "read"): CapabilityAction.CONFIG_READ,
    ("squads", "write"): CapabilityAction.CONFIG_WRITE,
    ("tasks", "read"): CapabilityAction.MEMORY_READ,
    ("tasks", "write"): CapabilityAction.MEMORY_WRITE,
    ("skills", "read"): CapabilityAction.TOOL_EXECUTE,
    ("skills", "register"): CapabilityAction.TOOL_REGISTER,
    ("skills", "execute"): CapabilityAction.TOOL_EXECUTE,
    ("state", "read"): CapabilityAction.MEMORY_READ,
    ("state", "write"): CapabilityAction.MEMORY_WRITE,
    ("pipeline", "read"): CapabilityAction.CONFIG_READ,
    ("pipeline", "write"): CapabilityAction.CONFIG_WRITE,
    ("pipeline", "execute"): CapabilityAction.TOOL_EXECUTE,
}


@dataclass
class AuthContext:
    token: str
    identity: Identity
    tenant_id: str | None
    is_system: bool = False

    @property
    def tenant_scope(self) -> str | None:
        return None if self.is_system else (self.tenant_id or DEFAULT_TENANT_ID)


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _is_bcrypt_hash(value: str) -> bool:
    return value.startswith("$2a$") or value.startswith("$2b$") or value.startswith("$2y$")


def _verify_token(token: str, stored_hash: str) -> bool:
    if _is_bcrypt_hash(stored_hash):
        try:
            return bcrypt.checkpw(token.encode("utf-8"), stored_hash.encode("utf-8"))
        except ValueError:
            return False
    legacy_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy_hash, stored_hash)


def _identity_from_snapshot(snapshot: dict) -> Identity:
    """Rebuild an Identity from a cached {tenant_id, identity_type} snapshot.

    Same construction as _identity_from_row — kept in one place so the cache
    cannot drift from the row path.
    """
    tenant_id = snapshot["tenant_id"]
    identity_type = snapshot["identity_type"]
    if identity_type == "agent":
        identity = AgentIdentity(name=tenant_id, model="api-key")
    elif identity_type == "service":
        identity = Identity(id=f"service:{tenant_id}", type=IdentityType.SERVICE, name=tenant_id)
    else:
        identity = UserIdentity(name=tenant_id)
    identity.metadata["tenant_id"] = tenant_id
    identity.metadata["identity_type"] = identity_type
    return identity


def _identity_from_row(row: sqlite3.Row) -> Identity:
    tenant_id = row["tenant_id"]
    identity_type = row["identity_type"]
    if identity_type == "agent":
        identity = AgentIdentity(name=tenant_id, model="api-key")
    elif identity_type == "service":
        identity = Identity(id=f"service:{tenant_id}", type=IdentityType.SERVICE, name=tenant_id)
    else:
        identity = UserIdentity(name=tenant_id)
    identity.metadata["tenant_id"] = tenant_id
    identity.metadata["identity_type"] = identity_type
    return identity


def _resource_name(resource: str, tenant_id: str | None) -> str:
    scope = tenant_id or "*"
    return f"squad:{scope}:{resource}"


def _capability_for(identity: Identity, tenant_id: str | None, action: CapabilityAction) -> Capability:
    return Capability(
        subject=identity.id,
        action=action,
        resource=_resource_name("*", tenant_id),
        issuer=SYSTEM_IDENTITY.id,
    )


# Token-verification cache — 2026-07-27 incident fix, hardened 2026-07-27
# against sos-205-a7c2fc44 adversarial gate findings (BLOCK-1/2/3, WARN-2/3).
#
# _lookup_token bcrypt-checks the presented token against EVERY api_keys row
# (17 bcrypt rows ≈ 5s of CPU) synchronously. Callers that retry on timeout
# (loop.py skill registration, hermes check-in) turned one slow verify into a
# congestion collapse: the service pegged a core and stopped answering while
# healthy clients piled on more verifies. The cache fixes the ACCIDENTAL case
# (same bad token replayed). It does NOT fix the adversarial case (unique-token
# spray, one full scan each) — require_capability now offloads the scan to a
# worker thread (BLOCK-1) so a spray blocks a thread-pool slot, not the event
# loop; the durable fix is still an indexed token-fingerprint column written at
# mint time (follow-up: "squad auth: indexed token fingerprint column at mint
# time", tracked on Mumega-com/sos).
#
# Cache keyed by a domain-separated hash of the token (WARN-3 — the previous
# bare sha256(token) was byte-identical to the legacy stored-credential format,
# so a cache key doubled as a credential against any row still on that legacy
# hash) so raw tokens never sit in memory beyond the request.
#
# Positive and negative hits live in SEPARATE bounded pools (BLOCK-3 — a single
# shared pool let an attacker spray unique bad tokens to evict live positive
# entries, since eviction picked oldest-by-expiry and every positive lives
# longer than a negative). A spray can now only evict other negatives.
#
# Positive TTL dropped 300s -> 30s (BLOCK-2). 300s on a surface with NO
# revocation mechanism was an unbounded trade-off, not a bounded one: a
# revoked-but-cached token could keep registering/executing skills for the
# full window. 30s still amortizes the ~1.2s bcrypt cost at real request
# rates while killing most of the stale-validity blast radius; the remaining
# window is closed on-demand by revoke_api_key(), which clears the cache
# outright. Negative TTL stays 60s (one bad-token client still costs one scan
# per minute, not one per request).
#
# _TOKEN_CACHE_LOCK guards ALL reads/writes to both pools. This was
# incidentally safe before because _lookup_token was fully synchronous and
# uvicorn ran a single worker — no interleaved dict mutation was possible.
# Offloading the scan to a thread pool (BLOCK-1) makes the cache genuinely
# shared mutable state across threads, so the lock is now load-bearing, not
# defensive.
_TOKEN_CACHE_POSITIVE: dict[str, tuple[float, dict]] = {}
_TOKEN_CACHE_NEGATIVE: dict[str, tuple[float, None]] = {}
_TOKEN_CACHE_LOCK = threading.Lock()
_TOKEN_CACHE_POSITIVE_TTL_S = 30.0
_TOKEN_CACHE_NEGATIVE_TTL_S = 60.0
_TOKEN_CACHE_POSITIVE_MAX = 64
_TOKEN_CACHE_NEGATIVE_MAX = 192

# Backward-compat alias for external/test code that still refers to the old
# single-cap constant name; both pools are individually bounded above.
_TOKEN_CACHE_MAX = _TOKEN_CACHE_POSITIVE_MAX + _TOKEN_CACHE_NEGATIVE_MAX

# Single-flight in-flight map (BLOCK-1b — sos-205-b5307dd7 re-gate). Keyed
# identically to the cache pools. Concurrent replays of the SAME cold token
# used to each run their own full-table bcrypt scan (measured: 8 concurrent
# lookups of one token -> 8 scans, vs 1 scan when sequential) because the
# cache-miss check and the scan were not atomic with respect to each other.
# The first caller for a cache_key registers a Future here (under
# _TOKEN_CACHE_LOCK, so registration is atomic with the miss check) and does
# the scan; every other caller for the same key waits on that Future instead
# of scanning again. Evicted as soon as the leader resolves it.
_TOKEN_CACHE_INFLIGHT: dict[str, "concurrent.futures.Future[dict | None]"] = {}


def _token_cache_key(token: str, db: SquadDB) -> str:
    # Domain-separated (WARN-3): must never collide with the legacy stored
    # hash format (bare sha256(token)) used by _verify_token's legacy path.
    # WARN-1 (sos-205-b5307dd7 re-gate): scoped to `db` too — the cache used
    # to be keyed on the token alone, so two distinct SquadDB instances
    # (e.g. a future per-tenant DB split) could share cache entries and a
    # token valid only in DB A would authenticate against DB B without ever
    # touching it. `_lookup_token(token, db)` and `revoke_api_key(tenant,
    # db=...)` both already advertise a per-db contract; this makes the
    # cache honor it instead of ignoring the db parameter.
    return hashlib.sha256(
        b"squad-authcache-v1:" + str(db.db_path).encode("utf-8") + b":" + token.encode("utf-8")
    ).hexdigest()


def _token_cache_get(key: str) -> tuple[bool, dict | None]:
    """Returns (hit, row_snapshot_or_None). Expired entries count as miss."""
    with _TOKEN_CACHE_LOCK:
        for pool in (_TOKEN_CACHE_POSITIVE, _TOKEN_CACHE_NEGATIVE):
            entry = pool.get(key)
            if entry is None:
                continue
            expires_at, snapshot = entry
            if time.monotonic() >= expires_at:
                pool.pop(key, None)
                return False, None
            return True, snapshot
        return False, None


def _token_cache_put(key: str, snapshot: dict | None) -> None:
    is_positive = snapshot is not None
    ttl = _TOKEN_CACHE_POSITIVE_TTL_S if is_positive else _TOKEN_CACHE_NEGATIVE_TTL_S
    pool = _TOKEN_CACHE_POSITIVE if is_positive else _TOKEN_CACHE_NEGATIVE
    other_pool = _TOKEN_CACHE_NEGATIVE if is_positive else _TOKEN_CACHE_POSITIVE
    cap = _TOKEN_CACHE_POSITIVE_MAX if is_positive else _TOKEN_CACHE_NEGATIVE_MAX
    with _TOKEN_CACHE_LOCK:
        # A key can only ever be positive or negative at once — drop it from
        # the other pool first (e.g. a token that was negative-cached and has
        # just verified).
        other_pool.pop(key, None)
        if len(pool) >= cap:
            oldest = min(pool, key=lambda k: pool[k][0])
            pool.pop(oldest, None)
        pool[key] = (time.monotonic() + ttl, snapshot)


def _token_cache_forget(key: str) -> None:
    """Drop a single key from both pools, wherever it lives."""
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE_POSITIVE.pop(key, None)
        _TOKEN_CACHE_NEGATIVE.pop(key, None)


def _token_cache_clear() -> None:
    """Drop the ENTIRE cache — both pools.

    Used by revoke_api_key() and rotation. Raw tokens are never stored (only
    the domain-separated hash), so revocation cannot target the specific
    cache entry for a revoked token — a whole-cache clear is the only sound
    option at this scale (<=256 entries total, cheap to rebuild on the next
    lookup).
    """
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE_POSITIVE.clear()
        _TOKEN_CACHE_NEGATIVE.clear()


def _context_from_snapshot(token: str, snapshot: dict | None) -> AuthContext | None:
    if snapshot is None:
        return None
    return AuthContext(
        token=token,
        identity=_identity_from_snapshot(snapshot),
        tenant_id=snapshot["tenant_id"],
        is_system=False,
    )


def _scan_and_cache(token: str, db: SquadDB, cache_key: str) -> dict | None:
    """The actual full-table bcrypt scan. Only ever called by the single-flight
    leader in _lookup_token — never call this directly from a second thread
    for the same cache_key."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT token_hash, tenant_id, identity_type, created_at FROM api_keys"
        ).fetchall()
        matched: sqlite3.Row | None = None
        legacy_hash: str | None = None
        for row in rows:
            stored_hash = row["token_hash"]
            if _verify_token(token, stored_hash):
                matched = row
                if not _is_bcrypt_hash(stored_hash):
                    legacy_hash = stored_hash
                break
        if matched and legacy_hash:
            conn.execute(
                "UPDATE api_keys SET token_hash = ? WHERE token_hash = ?",
                (hash_token(token), legacy_hash),
            )
    if not matched:
        _token_cache_put(cache_key, None)
        return None
    snapshot = {
        "tenant_id": matched["tenant_id"],
        "identity_type": matched["identity_type"],
    }
    _token_cache_put(cache_key, snapshot)
    return snapshot


def _lookup_token(token: str, db: SquadDB) -> AuthContext | None:
    if token == SYSTEM_TOKEN:
        return AuthContext(token=token, identity=SYSTEM_IDENTITY, tenant_id=None, is_system=True)
    cache_key = _token_cache_key(token, db)
    hit, snapshot = _token_cache_get(cache_key)
    if hit:
        return _context_from_snapshot(token, snapshot)

    # Single-flight (BLOCK-1b): register-or-join under the SAME lock that
    # guards the cache pools, so there is no window between the miss check
    # above and the Future registration where a second caller could slip in
    # and start its own scan.
    with _TOKEN_CACHE_LOCK:
        future = _TOKEN_CACHE_INFLIGHT.get(cache_key)
        if future is None:
            future = concurrent.futures.Future()
            _TOKEN_CACHE_INFLIGHT[cache_key] = future
            is_leader = True
        else:
            is_leader = False

    if not is_leader:
        # A scan for this exact cache_key is already running — wait for it
        # instead of running a second one. Propagates the leader's exception
        # (e.g. a DB error) to every follower too.
        snapshot = future.result()
        return _context_from_snapshot(token, snapshot)

    try:
        snapshot = _scan_and_cache(token, db, cache_key)
    except BaseException as exc:
        with _TOKEN_CACHE_LOCK:
            _TOKEN_CACHE_INFLIGHT.pop(cache_key, None)
        future.set_exception(exc)
        raise
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE_INFLIGHT.pop(cache_key, None)
    future.set_result(snapshot)
    return _context_from_snapshot(token, snapshot)


async def lookup_token(token: str, db: SquadDB) -> AuthContext | None:
    """Async, event-loop-safe entry point for token lookup — the ONLY place
    that should offload _lookup_token's scan to a worker thread.

    BLOCK-1 fix (sos-205-b5307dd7 re-gate): the a7c2fc44 fix offloaded the
    scan only inside require_capability's dependency; 34 other app.py route
    handlers called the synchronous _lookup_token directly, so the scan ran
    ON the event loop for the entire RBAC/CRM/referrals surface. Measured:
    27,956ms of dead event loop (heartbeat scheduled exactly once) for a
    single unpatched replay. Every caller — require_capability included —
    now goes through this one function, so there is exactly one offload
    point to keep correct, not one-per-caller.
    """
    return await anyio.to_thread.run_sync(_lookup_token, token, db)


def revoke_api_key(tenant_id: str, db: SquadDB | None = None) -> int:
    """Revoke every api_key row for ``tenant_id`` and invalidate the cache.

    BLOCK-2 fix (sos-205-a7c2fc44 adversarial gate): the service had no
    revocation path at all — a deleted row could still authenticate for up to
    300s because nothing ever cleared the in-process positive cache. This is
    the missing invalidation hook. It clears the ENTIRE _TOKEN_CACHE (both
    pools), not a per-entry lookup: raw tokens are never stored, only a
    one-way hash, so there is no way to identify which cache entries belong
    to the revoked tenant. A whole-cache clear is correct and cheap at this
    scale (<=256 entries; the next request just repopulates its own entry).

    Returns the number of rows deleted.
    """
    database = db or SquadDB()
    with database.connect() as conn:
        cursor = conn.execute(
            "DELETE FROM api_keys WHERE tenant_id = ?",
            (tenant_id,),
        )
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
    _token_cache_clear()
    return deleted


async def _emit_squad_policy(
    *,
    agent: str,
    action: str,
    target: str,
    tenant: str,
    allowed: bool,
    reason: str,
) -> None:
    """Emit one POLICY_DECISION event on the unified audit spine.

    v0.5.5 — squad's native capability check keeps enforcing permissions;
    this wrapper only ensures the decision lands in the kernel audit log
    alongside every other service. Never raises — audit failures never
    break a request.
    """
    try:
        from sos.contracts.audit import AuditDecision, AuditEventKind
        from sos.kernel.audit import append_event, new_event

        await append_event(
            new_event(
                agent=agent,
                tenant=tenant,
                kind=AuditEventKind.POLICY_DECISION,
                action=action,
                target=target,
                decision=AuditDecision.ALLOW if allowed else AuditDecision.DENY,
                reason=reason,
                policy_tier="squad_capability",
            )
        )
    except Exception:
        pass


def require_capability(resource: str, operation: str, db: SquadDB | None = None) -> Callable[[Request, HTTPAuthorizationCredentials | None], AuthContext]:
    action = OPERATION_MAP[(resource, operation)]
    database = db or SquadDB()
    action_name = f"squad:{resource}_{operation}"

    async def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> AuthContext:
        token = credentials.credentials if credentials else ""
        if not token:
            await _emit_squad_policy(
                agent="anonymous",
                action=action_name,
                target=resource,
                tenant="unknown",
                allowed=False,
                reason="missing_authorization",
            )
            raise HTTPException(status_code=401, detail="missing_authorization")
        # BLOCK-1 fix (sos-205-a7c2fc44 adversarial gate, generalized in
        # sos-205-b5307dd7): the offload lives in lookup_token() now — this
        # dependency is just one of its many callers (app.py's 34 direct
        # call sites go through the same function), not a special case.
        auth = await lookup_token(token, database)
        if not auth:
            await _emit_squad_policy(
                agent="anonymous",
                action=action_name,
                target=resource,
                tenant="unknown",
                allowed=False,
                reason="invalid_token",
            )
            raise HTTPException(status_code=401, detail="invalid_token")
        if auth.is_system:
            await _emit_squad_policy(
                agent=auth.identity.id,
                action=action_name,
                target=resource,
                tenant="mumega",
                allowed=True,
                reason="system_token",
            )
            return auth
        # TODO: Capability signatures are still not enforced on these request paths.
        # This route currently verifies identity + action mapping only; signed/delegated
        # capability verification needs a broader kernel-integrated rollout.
        capability = _capability_for(auth.identity, auth.tenant_id, action)
        ok, reason = verify_capability(capability, action, _resource_name(resource, auth.tenant_id))
        tenant_for_audit = auth.tenant_id or "mumega"
        if not ok:
            await _emit_squad_policy(
                agent=auth.identity.id,
                action=action_name,
                target=resource,
                tenant=tenant_for_audit,
                allowed=False,
                reason=reason or "capability_denied",
            )
            raise HTTPException(status_code=403, detail=reason)
        await _emit_squad_policy(
            agent=auth.identity.id,
            action=action_name,
            target=resource,
            tenant=tenant_for_audit,
            allowed=True,
            reason="capability_ok",
        )
        return auth

    return dependency


def create_api_key(
    tenant_id: str,
    identity_type: str = "user",
    db: SquadDB | None = None,
    rotate: bool = False,
) -> tuple[str, str]:
    """Mint a new api_key row for ``tenant_id``.

    WARN-2 fix (sos-205-a7c2fc44 adversarial gate): this used to be
    ``INSERT OR REPLACE`` keyed on ``token_hash`` — but bcrypt salts are
    random, so a freshly minted token_hash is never equal to an existing row
    and ``OR REPLACE`` was dead code. Every historical key stayed live
    forever (no revocation path existed either — see revoke_api_key / BLOCK-2)
    and the table grew monotonically, which is also the actual root cause of
    BLOCK-1's scan cost growing without bound.

    Default mint is now a plain, additive INSERT — existing rows for this
    tenant/identity_type are left alone (unchanged default behaviour from the
    caller's perspective; the only change is that duplicate token_hash
    collisions can no longer silently clobber each other, which they never
    did anyway). Pass ``rotate=True`` to explicitly retire this tenant's
    existing keys of the same identity_type before minting the replacement —
    real revocation, not an accidental hash-collision no-op.

    The durable fix for unbounded table growth is the indexed
    token-fingerprint column (follow-up: "squad auth: indexed token
    fingerprint column at mint time", tracked on Mumega-com/sos); rotation
    only bounds growth for callers that opt in.
    """
    database = db or SquadDB()
    token = f"sk-squad-{tenant_id}-{secrets.token_hex(16)}"
    token_hash = hash_token(token)
    created_at = now_iso()
    with database.connect() as conn:
        if rotate:
            conn.execute(
                "DELETE FROM api_keys WHERE tenant_id = ? AND identity_type = ?",
                (tenant_id, identity_type),
            )
        conn.execute(
            """
            INSERT INTO api_keys (token_hash, tenant_id, identity_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, tenant_id, identity_type, created_at),
        )
    if rotate:
        # Old keys for this tenant/identity_type just died; their cache
        # entries (if any) can't be targeted individually (see
        # _token_cache_clear docstring), so drop the whole cache.
        _token_cache_clear()
    # A brand-new token may sit in the negative cache from a pre-mint probe;
    # clear so it authenticates immediately.
    _token_cache_forget(_token_cache_key(token, database))
    return token, created_at


def _revoke_via_service(tenant_id: str, squad_url: str, system_token: str) -> bool:
    """POST /auth/revoke on the LIVE running service so its in-process cache
    is actually cleared — the CLI's own cache is a separate, always-empty
    process and clearing it (what the old code did) clears nothing real.

    Returns True only on a genuine 200 from the service. False on any
    failure (no token configured, connection refused, timeout, non-200) —
    the caller must never report a cleared cache on False (BLOCK-2 fix,
    sos-205-b5307dd7 re-gate: the old CLI printed cache_cleared=true
    unconditionally, which was a false receipt — it asserted a property
    about the SERVING process while only ever touching its own).
    """
    if not system_token:
        return False
    try:
        resp = requests.post(
            f"{squad_url.rstrip('/')}/auth/revoke",
            json={"tenant_id": tenant_id},
            headers={"Authorization": f"Bearer {system_token}"},
            timeout=5,
        )
    except requests.RequestException:
        return False
    return resp.status_code == 200


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Squad Service auth tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate a tenant API key")
    generate.add_argument("--tenant", required=True, help="Tenant identifier")
    generate.add_argument("--identity-type", default="user", choices=["user", "agent", "service"])
    generate.add_argument(
        "--rotate",
        action="store_true",
        help="Retire this tenant's existing keys of the same identity-type before minting",
    )

    # BLOCK-2 fix (sos-205-a7c2fc44 adversarial gate): the service had no
    # revoke path at all — this is it.
    revoke = sub.add_parser("revoke", help="Revoke all API keys for a tenant")
    revoke.add_argument("--tenant", required=True, help="Tenant identifier")

    args = parser.parse_args()
    if args.command == "generate":
        token, created_at = create_api_key(
            args.tenant, args.identity_type, rotate=args.rotate
        )
        print(f"api_key={token}")
        print(f"tenant_id={args.tenant}")
        print(f"identity_type={args.identity_type}")
        print("permissions=tenant-scoped kernel capabilities")
        print(f"created_at={created_at}")
        print(f"rotated={args.rotate}")
        return 0
    if args.command == "revoke":
        deleted = revoke_api_key(args.tenant)
        print(f"tenant_id={args.tenant}")
        print(f"revoked_rows={deleted}")
        # BLOCK-2 fix (sos-205-b5307dd7 re-gate): revoke_api_key() above only
        # clears the CLI's OWN in-process cache, which is always empty — the
        # CLI is a separate process from the running uvicorn service and has
        # no way to reach the service's _TOKEN_CACHE_POSITIVE/_NEGATIVE
        # directly. Printing cache_cleared=true unconditionally (the old
        # behaviour) was a false receipt: a revoked-but-cached token kept
        # authenticating against the live service for up to the positive TTL
        # (measured: 30s). Try the service's own /auth/revoke route first —
        # that runs in-process on the server and can actually clear it. Only
        # fall back to "we deleted the DB rows but can't prove the cache is
        # clear" when the service is unreachable.
        squad_url = os.getenv("SQUAD_URL", "http://localhost:8060")
        if _revoke_via_service(args.tenant, squad_url, SYSTEM_TOKEN):
            print("cache_cleared=service")
        else:
            print(
                "cache_cleared=LOCAL-PROCESS-ONLY — running service still holds "
                "cached entries up to 30s TTL; hit POST /auth/revoke or restart"
            )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
