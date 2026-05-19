from typing import Callable, Optional
from datetime import datetime, timezone
import fnmatch
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from sos.kernel.policy.capability import CapabilityModel
from sos.kernel.policy.fmaap import FMAAPPolicyEngine, FMAAPValidationRequest
from sos.kernel.capability import verify_capability_signature
from sos.observability.logging import get_logger

log = get_logger("capability_guard")
fmaap = FMAAPPolicyEngine()

# Known issuer public keys (in production, load from secure storage)
# Format: {issuer_id: public_key_bytes}
ISSUER_PUBLIC_KEYS: dict[str, bytes] = {
    # Add trusted issuer keys here
    # "river": bytes.fromhex("...32-byte-ed25519-public-key...")
}

async def capability_guard_middleware(request: Request, call_next: Callable) -> Response:
    """
    Middleware to enforce FMAAP and Capability-Based Security.
    """
    path = request.url.path
    if path in ["/health", "/metrics", "/docs", "/openapi.json"]:
        return await call_next(request)

    # For Phase 1, we perform a background FMAAP validation
    # to witness the resonance without blocking.
    agent_id = request.headers.get("X-SOS-Agent-ID", "unknown")
    
    validation = fmaap.validate(FMAAPValidationRequest(
        agent_id=agent_id,
        action="api_request",
        resource=path
    ))
    
    if not validation.valid:
        log.warn(f"FMAAP Validation FAILED for {agent_id}", score=validation.overall_score)
        # In strict mode, we would return 403 here.
    else:
        log.info(f"FMAAP Validation PASSED", score=validation.overall_score)
    
    response = await call_next(request)
    return response

class CapabilityVerifier:
    """
    Service-level verifier for capabilities with Ed25519 signature verification.
    """

    def __init__(self, issuer_keys: Optional[dict[str, bytes]] = None):
        """
        Initialize verifier with trusted issuer public keys.

        Args:
            issuer_keys: Mapping of issuer ID to Ed25519 public key (32 bytes)
        """
        self.issuer_keys = issuer_keys or ISSUER_PUBLIC_KEYS

    def verify(self, capability: CapabilityModel, action: str, resource: str) -> bool:
        """
        Verify if a capability grants permission for an action.

        Checks:
        1. Action matches (or is wildcard)
        2. Resource matches (or is wildcard)
        3. Capability has not expired
        4. Signature is valid (if issuer key is known)

        Returns:
            True if capability is valid and grants permission
        """
        # Check action match
        if capability.action != "*" and capability.action != action:
            log.warn(f"Capability action mismatch: {capability.action} != {action}")
            return False

        # Check resource match (supports glob patterns)
        if capability.resource != "*" and not self._matches_resource(capability.resource, resource):
            log.warn(f"Capability resource mismatch: {capability.resource} != {resource}")
            return False

        # Check expiry
        if not self._check_expiry(capability):
            return False

        # Check signature
        if not self._verify_signature(capability):
            return False

        return True

    def _matches_resource(self, pattern: str, resource: str) -> bool:
        """Check if resource matches capability pattern (supports glob)."""
        # Exact match
        if pattern == resource:
            return True
        # Glob pattern match (replace ** with *)
        glob_pattern = pattern.replace("**", "*")
        return fnmatch.fnmatchcase(resource, glob_pattern)

    def _check_expiry(self, capability: CapabilityModel) -> bool:
        """Check if capability has expired."""
        try:
            expires_at = datetime.fromisoformat(capability.expires_at.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if now > expires_at:
                log.warn(
                    f"Capability {capability.id} has expired",
                    expires_at=capability.expires_at,
                    now=now.isoformat()
                )
                return False
        except (ValueError, AttributeError) as e:
            log.warn(f"Invalid expiry format in capability {capability.id}: {e}")
            return False
        return True

    def _verify_signature(self, capability: CapabilityModel) -> bool:
        """Verify capability signature against issuer's public key."""
        # If no signature, reject in strict mode
        if not capability.signature:
            # For backwards compatibility, allow unsigned caps if issuer not in trusted keys
            if capability.issuer not in self.issuer_keys:
                log.debug(f"No signature and issuer {capability.issuer} not in trusted keys, allowing")
                return True
            log.warn(f"Capability {capability.id} missing signature from trusted issuer {capability.issuer}")
            return False

        # Get issuer's public key
        public_key = self.issuer_keys.get(capability.issuer)
        if public_key is None:
            # Issuer not known - in strict mode would reject, allow for now
            log.debug(f"Issuer {capability.issuer} not in trusted keys, skipping signature check")
            return True

        # Convert to kernel Capability for signature verification
        try:
            kernel_cap = capability.to_capability()
            valid, reason = verify_capability_signature(kernel_cap, public_key)
            if not valid:
                log.warn(
                    f"Capability {capability.id} signature verification failed",
                    reason=reason,
                    issuer=capability.issuer
                )
                return False
        except Exception as e:
            log.error(f"Signature verification error for capability {capability.id}: {e}")
            return False

        return True


# Default verifier instance
capability_verifier = CapabilityVerifier()
