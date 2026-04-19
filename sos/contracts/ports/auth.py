"""AuthPort — session resolution for incoming Worker requests.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how a plugin asks "who is this caller?".

Tenant binding: the port itself is tenant-agnostic — the returned AuthUser
carries `tenant_slug` once resolved. Callers use that to key subsequent
tenant-aware port calls.

Python Note on `request_token`
------------------------------
Inkwell's AuthPort takes a raw Fetch `Request`. Python has no universal
equivalent, so we pass an opaque `request_token` string (typically the raw
`Authorization` header value, or a signed session cookie). Adapters MAY
accept richer request objects via subclasses — the contract only requires
a string is enough.
"""
from __future__ import annotations

from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


Role = Literal["owner", "admin", "manager", "member", "viewer"]


# --- Request / response models ---------------------------------------------


class AuthUser(BaseModel):
    """Resolved caller identity. Returned by AuthPort."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str
    tenant_slug: Optional[str] = Field(
        default=None,
        description="Tenant the caller belongs to — None for platform-level users.",
    )
    role: Optional[Role] = None


class GetUserRequest(BaseModel):
    """Input to getUser / requireUser."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_token: str = Field(
        min_length=1,
        description="Raw Authorization header value or signed session cookie.",
    )


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class AuthPort(Protocol):
    """Session resolution."""

    async def get_user(self, req: GetUserRequest) -> Optional[AuthUser]:
        """Resolve the caller — None if unauthenticated."""
        ...

    async def require_user(self, req: GetUserRequest) -> AuthUser:
        """Same as get_user but raises (HTTP 401 in the adapter) if unresolved."""
        ...


__all__ = [
    "Role",
    "AuthUser",
    "GetUserRequest",
    "AuthPort",
]
