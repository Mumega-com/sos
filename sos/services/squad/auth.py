from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sos.kernel.capability import Capability, CapabilityAction, verify_capability
from sos.kernel.identity import SYSTEM_IDENTITY, AgentIdentity, Identity, IdentityType, UserIdentity
from sos.services.squad.service import DEFAULT_TENANT_ID, SquadDB, now_iso


SYSTEM_TOKEN = os.getenv("SOS_SYSTEM_TOKEN", "")  # REQUIRED — no default
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
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _lookup_token(token: str, db: SquadDB) -> AuthContext | None:
    if token == SYSTEM_TOKEN:
        return AuthContext(token=token, identity=SYSTEM_IDENTITY, tenant_id=None, is_system=True)
    token_hash = hash_token(token)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT token_hash, tenant_id, identity_type, created_at FROM api_keys WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    if not row:
        return None
    return AuthContext(token=token, identity=_identity_from_row(row), tenant_id=row["tenant_id"], is_system=False)


def require_capability(resource: str, operation: str, db: SquadDB | None = None) -> Callable[[Request, HTTPAuthorizationCredentials | None], AuthContext]:
    action = OPERATION_MAP[(resource, operation)]
    database = db or SquadDB()

    async def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> AuthContext:
        token = credentials.credentials if credentials else ""
        if not token:
            raise HTTPException(status_code=401, detail="missing_authorization")
        auth = _lookup_token(token, database)
        if not auth:
            raise HTTPException(status_code=401, detail="invalid_token")
        if auth.is_system:
            return auth
        capability = _capability_for(auth.identity, auth.tenant_id, action)
        ok, reason = verify_capability(capability, action, _resource_name(resource, auth.tenant_id))
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        return auth

    return dependency


def create_api_key(tenant_id: str, identity_type: str = "user", db: SquadDB | None = None) -> tuple[str, str]:
    database = db or SquadDB()
    token = f"sk-squad-{tenant_id}-{secrets.token_hex(16)}"
    token_hash = hash_token(token)
    created_at = now_iso()
    with database.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO api_keys (token_hash, tenant_id, identity_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, tenant_id, identity_type, created_at),
        )
    return token, created_at


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Squad Service auth tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate a tenant API key")
    generate.add_argument("--tenant", required=True, help="Tenant identifier")
    generate.add_argument("--identity-type", default="user", choices=["user", "agent", "service"])

    args = parser.parse_args()
    if args.command == "generate":
        token, created_at = create_api_key(args.tenant, args.identity_type)
        print(f"api_key={token}")
        print(f"tenant_id={args.tenant}")
        print(f"identity_type={args.identity_type}")
        print("permissions=tenant-scoped kernel capabilities")
        print(f"created_at={created_at}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
