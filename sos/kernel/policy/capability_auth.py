from __future__ import annotations

import base64
import json
import os
from typing import Optional

from fastapi import HTTPException, Request

from sos.kernel import CapabilityAction, verify_capability
from sos.kernel.policy.capability import CapabilityModel


CAPABILITY_HEADER = "X-SOS-Capability"


def _env_truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def decode_capability_header(value: str) -> CapabilityModel:
    """
    Decode a capability token from an HTTP header value.

    Supported formats:
    - Base64URL-encoded JSON (recommended)
    - Raw JSON (for local debugging)
    """
    token = value.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()

    try:
        if token.startswith("{"):
            payload = json.loads(token)
        else:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid_capability_header") from e

    try:
        return CapabilityModel.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid_capability_payload") from e


def get_capability_from_request(request: Request) -> Optional[CapabilityModel]:
    """
    Extract a capability token from an incoming HTTP request.

    Header priority:
    1) `X-SOS-Capability`
    2) `Authorization: Bearer <token>`
    """
    if not _env_truthy("SOS_REQUIRE_CAPABILITIES", "0"):
        return None

    raw = request.headers.get(CAPABILITY_HEADER)
    if raw:
        return decode_capability_header(raw)

    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return decode_capability_header(auth)

    return None


def require_capability(
    capability: Optional[CapabilityModel],
    *,
    action: CapabilityAction,
    resource: str,
    expected_subject: Optional[str] = None,
) -> None:
    """
    Enforce a capability requirement for an operation.

    Enabled by setting `SOS_REQUIRE_CAPABILITIES=1`.
    """
    if not _env_truthy("SOS_REQUIRE_CAPABILITIES", "0"):
        return

    if capability is None:
        raise HTTPException(status_code=401, detail="missing_capability")

    public_key_hex = os.getenv("SOS_RIVER_PUBLIC_KEY_HEX") or os.getenv("SOS_CAPABILITY_PUBLIC_KEY_HEX")
    if not public_key_hex:
        raise HTTPException(status_code=500, detail="capability_public_key_not_configured")

    try:
        public_key = bytes.fromhex(public_key_hex)
    except ValueError:
        raise HTTPException(status_code=500, detail="invalid_public_key_hex")

    cap = capability.to_capability()
    if expected_subject is not None and cap.subject != expected_subject:
        raise HTTPException(status_code=403, detail="capability_subject_mismatch")

    ok, reason = verify_capability(cap, action, resource, public_key=public_key)
    if not ok:
        status_code = 401 if "signature" in reason.lower() else 403
        raise HTTPException(status_code=status_code, detail=reason)
