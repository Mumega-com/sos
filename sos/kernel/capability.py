"""
SOS Kernel Capability - Capability-based access control tokens.

Capabilities are unforgeable tokens that grant specific permissions.
They are:
- Time-limited (expire after a set duration)
- Scope-limited (specific actions on specific resources)
- Constraint-limited (rate limits, max amounts, etc.)
- Signed by the issuer (River or delegated authority)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
import fnmatch
import hashlib
import json
import uuid

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


class CapabilityAction(Enum):
    """Actions that can be authorized by capabilities."""
    # Memory actions
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_DELETE = "memory:delete"

    # Tool actions
    TOOL_EXECUTE = "tool:execute"
    TOOL_REGISTER = "tool:register"

    # Ledger actions
    LEDGER_READ = "ledger:read"
    LEDGER_WRITE = "ledger:write"

    # Agent actions
    AGENT_SPAWN = "agent:spawn"
    AGENT_TERMINATE = "agent:terminate"

    # Config actions
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"

    # Secret actions
    SECRET_READ = "secret:read"
    SECRET_WRITE = "secret:write"

    # Network actions
    NETWORK_OUTBOUND = "network:outbound"

    # File actions
    FILE_READ = "file:read"
    FILE_WRITE = "file:write"


@dataclass
class Capability:
    """
    Unforgeable capability token for access control.

    Attributes:
        id: Unique capability identifier
        subject: Who the capability is granted to (agent/service ID)
        action: What action is permitted
        resource: What resource the action applies to (glob patterns supported)
        constraints: Additional constraints (rate limits, max amounts, etc.)
        issued_at: When capability was issued
        expires_at: When capability expires
        issuer: Who issued the capability
        signature: Ed25519 signature of the capability
        uses_remaining: Optional limit on number of uses
        parent_id: Parent capability if delegated
    """
    subject: str
    action: CapabilityAction
    resource: str
    id: str = field(default_factory=lambda: f"cap_{uuid.uuid4().hex[:12]}")
    constraints: dict[str, Any] = field(default_factory=dict)
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=1))
    issuer: str = "river"
    signature: Optional[str] = None
    uses_remaining: Optional[int] = None
    parent_id: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        """Check if capability has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if capability is currently valid (not expired, has uses)."""
        if self.is_expired:
            return False
        if self.uses_remaining is not None and self.uses_remaining <= 0:
            return False
        return True

    @property
    def signing_payload(self) -> str:
        """Generate payload for signing/verification."""
        payload = {
            "id": self.id,
            "subject": self.subject,
            "action": self.action.value,
            "resource": self.resource,
            "constraints": self.constraints,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "issuer": self.issuer,
            "uses_remaining": self.uses_remaining,
            "parent_id": self.parent_id,
        }
        return json.dumps(payload, sort_keys=True)

    @property
    def hash(self) -> str:
        """Generate hash of capability for logging/reference."""
        return hashlib.sha256(self.signing_payload.encode()).hexdigest()[:16]

    def matches_resource(self, resource: str) -> bool:
        """
        Check if capability resource pattern matches given resource.

        Resource patterns use shell-style wildcards over the full resource string.
        Examples:
        - `memory:agent:kasra/*`
        - `tool:web_search`
        - `ledger:*`
        """
        pattern = self.resource.replace("**", "*")
        return fnmatch.fnmatchcase(resource, pattern)

    def use(self) -> bool:
        """
        Check if the capability can be used.

        Note: Capability tokens should be treated as immutable. Usage limits
        (uses remaining, revocation, etc.) should be enforced via a separate
        capability usage tracker/state machine.
        """
        if not self.is_valid:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize capability to dictionary."""
        return {
            "id": self.id,
            "subject": self.subject,
            "action": self.action.value,
            "resource": self.resource,
            "constraints": self.constraints,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "issuer": self.issuer,
            "signature": self.signature,
            "uses_remaining": self.uses_remaining,
            "parent_id": self.parent_id,
        }

    def to_json(self) -> str:
        """Serialize capability to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capability:
        """Deserialize capability from dictionary."""
        return cls(
            id=data["id"],
            subject=data["subject"],
            action=CapabilityAction(data["action"]),
            resource=data["resource"],
            constraints=data.get("constraints", {}),
            issued_at=datetime.fromisoformat(data["issued_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            issuer=data["issuer"],
            signature=data.get("signature"),
            uses_remaining=data.get("uses_remaining"),
            parent_id=data.get("parent_id"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> Capability:
        """Deserialize capability from JSON string."""
        return cls.from_dict(json.loads(json_str))


def verify_capability(
    capability: Capability,
    required_action: CapabilityAction,
    resource: str,
    public_key: Optional[bytes] = None,
) -> tuple[bool, str]:
    """
    Verify a capability is valid for the requested action and resource.

    Args:
        capability: The capability to verify
        required_action: The action being attempted
        resource: The resource being accessed
        public_key: Optional public key to verify signature

    Returns:
        Tuple of (is_valid, reason)
    """
    # Check expiration
    if capability.is_expired:
        return False, "Capability has expired"

    # Check uses remaining
    if capability.uses_remaining is not None and capability.uses_remaining <= 0:
        return False, "Capability has no uses remaining"

    # Check action matches
    if capability.action != required_action:
        return False, f"Capability action {capability.action.value} does not match required {required_action.value}"

    # Check resource matches
    if not capability.matches_resource(resource):
        return False, f"Capability resource {capability.resource} does not match {resource}"

    # Verify signature if public key provided
    if public_key is not None:
        ok, reason = verify_capability_signature(capability, public_key)
        if not ok:
            return False, reason

    return True, "Valid"


def sign_capability(capability: Capability, signing_key: SigningKey) -> str:
    """
    Sign a capability using Ed25519 and store the signature on the capability.

    Signature format: "ed25519:<hex>"
    """
    message = capability.signing_payload.encode("utf-8")
    signature = signing_key.sign(message).signature.hex()
    capability.signature = f"ed25519:{signature}"
    return capability.signature


def _parse_ed25519_signature(signature: str) -> bytes:
    if signature.startswith("ed25519:"):
        signature = signature.split(":", 1)[1]
    return bytes.fromhex(signature)


def verify_capability_signature(capability: Capability, public_key: bytes) -> tuple[bool, str]:
    """
    Verify a capability's Ed25519 signature against the provided public key.

    Args:
        capability: Capability to verify (must include signature)
        public_key: Ed25519 public key (32 raw bytes)

    Returns:
        Tuple of (is_valid, reason)
    """
    if not capability.signature:
        return False, "Capability is missing signature"

    try:
        signature_bytes = _parse_ed25519_signature(capability.signature)
    except ValueError:
        return False, "Invalid signature encoding"

    try:
        VerifyKey(public_key).verify(capability.signing_payload.encode("utf-8"), signature_bytes)
    except BadSignatureError:
        return False, "Invalid signature"
    except Exception as e:
        return False, f"Signature verification failed: {e}"

    return True, "Valid signature"


def create_capability(
    subject: str,
    action: CapabilityAction,
    resource: str,
    duration_hours: int = 1,
    constraints: Optional[dict] = None,
    uses: Optional[int] = None,
    issuer: str = "river",
) -> Capability:
    """
    Factory function to create a new capability.

    Args:
        subject: Who the capability is for
        action: What action to permit
        resource: What resource (supports globs)
        duration_hours: How long capability is valid
        constraints: Additional constraints
        uses: Optional limit on uses
        issuer: Who is issuing (default: river)

    Returns:
        New Capability instance
    """
    now = datetime.now(timezone.utc)
    return Capability(
        subject=subject,
        action=action,
        resource=resource,
        constraints=constraints or {},
        issued_at=now,
        expires_at=now + timedelta(hours=duration_hours),
        issuer=issuer,
        uses_remaining=uses,
    )


# Common capability templates
def memory_read_capability(
    subject: str,
    scope: str = "*",
    duration_hours: int = 1,
) -> Capability:
    """Create capability to read from memory."""
    return create_capability(
        subject=subject,
        action=CapabilityAction.MEMORY_READ,
        resource=f"memory:{scope}",
        duration_hours=duration_hours,
    )


def tool_execute_capability(
    subject: str,
    tool_name: str,
    rate_limit: str = "100/hour",
    duration_hours: int = 1,
) -> Capability:
    """Create capability to execute a tool."""
    return create_capability(
        subject=subject,
        action=CapabilityAction.TOOL_EXECUTE,
        resource=f"tool:{tool_name}",
        constraints={"rate_limit": rate_limit},
        duration_hours=duration_hours,
    )
