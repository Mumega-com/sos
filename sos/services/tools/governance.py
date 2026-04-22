from __future__ import annotations

import os
from typing import Iterable, Optional

from fastapi import HTTPException, Request

from sos.kernel import CapabilityAction, verify_capability
from sos.observability.audit import AuditLogger
from sos.services.common.auth import (
    CAPABILITY_HEADER,
    decode_capability_header,
)
from sos.services.common.capability import CapabilityModel

PROTECTED_PROVIDERS = {"gaf", "inkwell"}


def _public_key() -> bytes:
    key = os.getenv("SOS_RIVER_PUBLIC_KEY_HEX") or os.getenv("SOS_CAPABILITY_PUBLIC_KEY_HEX")
    if not key:
        raise HTTPException(status_code=500, detail="capability_public_key_not_configured")
    try:
        return bytes.fromhex(key)
    except ValueError:
        raise HTTPException(status_code=500, detail="invalid_public_key_hex")


def _capability_from_request(request: Request) -> Optional[CapabilityModel]:
    raw = request.headers.get(CAPABILITY_HEADER)
    if raw:
        return decode_capability_header(raw)

    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return decode_capability_header(auth)

    return None


def _scopes(capability: CapabilityModel) -> set[str]:
    raw = capability.constraints.get("scopes", [])
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(scope) for scope in raw}
    return set()


def provider_for_tool(tool_name: str) -> Optional[str]:
    normalized = tool_name.strip().lower()
    if normalized.startswith("mcp."):
        parts = normalized.split(".")
        if len(parts) >= 2 and parts[1] in PROTECTED_PROVIDERS:
            return parts[1]
    for provider in PROTECTED_PROVIDERS:
        if (
            normalized == provider
            or normalized.startswith(f"{provider}.")
            or normalized.startswith(f"{provider}_")
        ):
            return provider
        if normalized.startswith(f"plugin.{provider}."):
            return provider
    return None


def provider_for_mcp_server(server_name: str) -> Optional[str]:
    normalized = server_name.strip().lower()
    return normalized if normalized in PROTECTED_PROVIDERS else None


def resource_for_tool(tool_name: str) -> str:
    normalized = tool_name.strip().lower()
    if normalized.startswith("mcp."):
        _, server, *rest = normalized.split(".")
        return f"mcp:{server}/{'/'.join(rest) if rest else '*'}"
    provider = provider_for_tool(normalized) or "unknown"
    if normalized.startswith(f"{provider}."):
        return f"tool:{normalized}"
    if normalized.startswith(f"plugin.{provider}."):
        return f"tool:{provider}.{normalized.removeprefix(f'plugin.{provider}.')}"
    return f"tool:{provider}.{normalized}"


def resource_for_mcp_server(server_name: str) -> str:
    return f"mcp:{server_name.strip().lower()}/*"


def required_scopes_for_tool(tool_name: str) -> list[str]:
    provider = provider_for_tool(tool_name)
    if not provider:
        return []

    normalized = tool_name.lower()
    scopes = ["tools.execute"]
    if provider == "gaf":
        if any(
            term in normalized
            for term in (
                "write",
                "create",
                "update",
                "delete",
                "submit",
                "publish",
                "sync",
                "handoff",
            )
        ):
            scopes.append("gaf.write.commit")
        else:
            scopes.append("gaf.read")
    elif provider == "inkwell":
        if any(term in normalized for term in ("publish", "ingest")):
            scopes.append("inkwell.publish")
        elif any(term in normalized for term in ("write", "draft", "create", "update")):
            scopes.append("inkwell.draft")
        else:
            scopes.append("inkwell.read")
    return scopes


def required_scopes_for_mcp_server(server_name: str) -> list[str]:
    provider = provider_for_mcp_server(server_name)
    if not provider:
        return []
    if provider == "gaf":
        return ["tools.admin", "gaf.admin"]
    return ["tools.admin", "inkwell.publish"]


async def _deny(
    tool_name: str,
    reason: str,
    capability: Optional[CapabilityModel],
    required_scopes: Iterable[str],
) -> None:
    await AuditLogger().log_tool_denied(
        tool_name=tool_name,
        agent_id=capability.subject if capability else "unknown",
        reason=reason,
        capability_id=capability.id if capability else None,
        required_scopes=list(required_scopes),
    )


async def enforce_tool_execute(request: Request, tool_name: str) -> None:
    provider = provider_for_tool(tool_name)
    if not provider:
        return

    resource = resource_for_tool(tool_name)
    required_scopes = required_scopes_for_tool(tool_name)
    capability = _capability_from_request(request)
    if capability is None:
        await _deny(tool_name, "missing_capability", None, required_scopes)
        raise HTTPException(status_code=401, detail="missing_capability")

    cap = capability.to_capability()
    ok, reason = verify_capability(
        cap,
        CapabilityAction.TOOL_EXECUTE,
        resource,
        public_key=_public_key(),
    )
    if not ok:
        await _deny(tool_name, reason, capability, required_scopes)
        raise HTTPException(status_code=403, detail=reason)

    provided = _scopes(capability)
    missing = [scope for scope in required_scopes if scope not in provided]
    if missing:
        reason = f"missing_scopes:{','.join(missing)}"
        await _deny(tool_name, reason, capability, required_scopes)
        raise HTTPException(status_code=403, detail=reason)


async def enforce_mcp_register(request: Request, server_name: str) -> None:
    provider = provider_for_mcp_server(server_name)
    if not provider:
        return

    resource = resource_for_mcp_server(server_name)
    required_scopes = required_scopes_for_mcp_server(server_name)
    capability = _capability_from_request(request)
    if capability is None:
        await _deny(resource, "missing_capability", None, required_scopes)
        raise HTTPException(status_code=401, detail="missing_capability")

    cap = capability.to_capability()
    ok, reason = verify_capability(
        cap,
        CapabilityAction.TOOL_REGISTER,
        resource,
        public_key=_public_key(),
    )
    if not ok:
        await _deny(resource, reason, capability, required_scopes)
        raise HTTPException(status_code=403, detail=reason)

    provided = _scopes(capability)
    missing = [scope for scope in required_scopes if scope not in provided]
    if missing:
        reason = f"missing_scopes:{','.join(missing)}"
        await _deny(resource, reason, capability, required_scopes)
        raise HTTPException(status_code=403, detail=reason)
