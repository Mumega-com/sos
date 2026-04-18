"""
Tests for capability-based access control.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from nacl.signing import SigningKey

from sos.kernel.capability import (
    Capability,
    CapabilityAction,
    sign_capability,
    verify_capability,
    verify_capability_signature,
    create_capability,
    memory_read_capability,
    tool_execute_capability,
)


class TestCapability:
    """Tests for Capability dataclass."""

    def test_default_values(self):
        """Capability should have sensible defaults."""
        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )
        assert cap.id.startswith("cap_")
        assert cap.issuer == "river"
        assert cap.is_valid is True
        assert cap.signature is None

    def test_expired_capability(self):
        """Expired capability should be marked invalid."""
        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )
        assert cap.is_expired is True
        assert cap.is_valid is False

    def test_uses_remaining_exhausted(self):
        """Capability with no uses remaining should be invalid."""
        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.TOOL_EXECUTE,
            resource="tool:*",
            uses_remaining=0
        )
        assert cap.is_valid is False

    def test_resource_matching_exact(self):
        """Exact resource match should work."""
        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:agent:kasra"
        )
        assert cap.matches_resource("memory:agent:kasra") is True
        assert cap.matches_resource("memory:agent:river") is False

    def test_resource_matching_wildcard(self):
        """Wildcard resource patterns should work."""
        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:agent:*"
        )
        assert cap.matches_resource("memory:agent:kasra") is True
        assert cap.matches_resource("memory:agent:river") is True
        assert cap.matches_resource("memory:global") is False

    def test_serialization_roundtrip(self):
        """Capability should survive serialization/deserialization."""
        original = Capability(
            subject="agent:test",
            action=CapabilityAction.TOOL_EXECUTE,
            resource="tool:web_search",
            constraints={"rate_limit": "100/hour"}
        )

        data = original.to_dict()
        restored = Capability.from_dict(data)

        assert restored.subject == original.subject
        assert restored.action == original.action
        assert restored.resource == original.resource
        assert restored.constraints == original.constraints


class TestCapabilitySigning:
    """Tests for Ed25519 capability signing."""

    def test_sign_capability(self):
        """sign_capability should produce valid signature."""
        signing_key = SigningKey.generate()

        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )

        signature = sign_capability(cap, signing_key)

        assert signature.startswith("ed25519:")
        assert cap.signature == signature

    def test_verify_valid_signature(self):
        """Valid signature should verify successfully."""
        signing_key = SigningKey.generate()
        public_key = signing_key.verify_key.encode()

        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )
        sign_capability(cap, signing_key)

        valid, reason = verify_capability_signature(cap, public_key)
        assert valid is True
        assert reason == "Valid signature"

    def test_verify_invalid_signature(self):
        """Invalid signature should fail verification."""
        signing_key = SigningKey.generate()
        wrong_key = SigningKey.generate()
        wrong_public = wrong_key.verify_key.encode()

        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )
        sign_capability(cap, signing_key)

        valid, reason = verify_capability_signature(cap, wrong_public)
        assert valid is False
        assert "Invalid signature" in reason

    def test_verify_missing_signature(self):
        """Missing signature should fail verification."""
        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )

        public_key = SigningKey.generate().verify_key.encode()
        valid, reason = verify_capability_signature(cap, public_key)

        assert valid is False
        assert "missing signature" in reason.lower()

    def test_tampered_capability_fails(self):
        """Tampering with capability after signing should fail verification."""
        signing_key = SigningKey.generate()
        public_key = signing_key.verify_key.encode()

        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )
        sign_capability(cap, signing_key)

        # Tamper with the capability
        cap.resource = "memory:admin:*"  # Changed!

        valid, reason = verify_capability_signature(cap, public_key)
        assert valid is False


class TestVerifyCapability:
    """Tests for full capability verification."""

    def test_verify_valid_capability(self):
        """Valid capability should pass verification."""
        cap = create_capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:agent:*",
            duration_hours=1
        )

        valid, reason = verify_capability(
            cap,
            required_action=CapabilityAction.MEMORY_READ,
            resource="memory:agent:kasra"
        )

        assert valid is True
        assert reason == "Valid"

    def test_verify_expired_capability(self):
        """Expired capability should fail verification."""
        cap = Capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )

        valid, reason = verify_capability(
            cap,
            required_action=CapabilityAction.MEMORY_READ,
            resource="memory:test"
        )

        assert valid is False
        assert "expired" in reason.lower()

    def test_verify_wrong_action(self):
        """Wrong action should fail verification."""
        cap = create_capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )

        valid, reason = verify_capability(
            cap,
            required_action=CapabilityAction.MEMORY_WRITE,  # Different action!
            resource="memory:test"
        )

        assert valid is False
        assert "action" in reason.lower()

    def test_verify_wrong_resource(self):
        """Wrong resource should fail verification."""
        cap = create_capability(
            subject="agent:test",
            action=CapabilityAction.TOOL_EXECUTE,
            resource="tool:web_search"
        )

        valid, reason = verify_capability(
            cap,
            required_action=CapabilityAction.TOOL_EXECUTE,
            resource="tool:file_write"  # Different resource!
        )

        assert valid is False
        assert "resource" in reason.lower()

    def test_verify_with_signature_check(self):
        """Verification with signature check should work."""
        signing_key = SigningKey.generate()
        public_key = signing_key.verify_key.encode()

        cap = create_capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:*"
        )
        sign_capability(cap, signing_key)

        valid, reason = verify_capability(
            cap,
            required_action=CapabilityAction.MEMORY_READ,
            resource="memory:test",
            public_key=public_key
        )

        assert valid is True


class TestCapabilityFactory:
    """Tests for capability factory functions."""

    def test_create_capability(self):
        """create_capability should create valid capability."""
        cap = create_capability(
            subject="agent:test",
            action=CapabilityAction.MEMORY_WRITE,
            resource="memory:agent:test/*",
            duration_hours=2,
            constraints={"max_size": 1024},
            uses=10
        )

        assert cap.subject == "agent:test"
        assert cap.action == CapabilityAction.MEMORY_WRITE
        assert cap.resource == "memory:agent:test/*"
        assert cap.constraints == {"max_size": 1024}
        assert cap.uses_remaining == 10
        assert cap.is_valid is True

    def test_memory_read_capability(self):
        """memory_read_capability should create correct capability."""
        cap = memory_read_capability("agent:kasra", scope="agent:kasra/*")

        assert cap.subject == "agent:kasra"
        assert cap.action == CapabilityAction.MEMORY_READ
        assert cap.resource == "memory:agent:kasra/*"

    def test_tool_execute_capability(self):
        """tool_execute_capability should create correct capability."""
        cap = tool_execute_capability(
            "agent:kasra",
            tool_name="web_search",
            rate_limit="50/hour"
        )

        assert cap.subject == "agent:kasra"
        assert cap.action == CapabilityAction.TOOL_EXECUTE
        assert cap.resource == "tool:web_search"
        assert cap.constraints["rate_limit"] == "50/hour"


class TestCapabilityVerifierMiddleware:
    """Tests for CapabilityVerifier in middleware."""

    def test_verifier_checks_expiry(self):
        """Verifier should reject expired capabilities."""
        from sos.services.engine.middleware import CapabilityVerifier
        from sos.kernel.policy.capability import CapabilityModel

        verifier = CapabilityVerifier()

        expired_cap = CapabilityModel(
            id="cap_test",
            subject="agent:test",
            action="memory:read",
            resource="memory:*",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            issuer="test"
        )

        result = verifier.verify(expired_cap, "memory:read", "memory:test")
        assert result is False

    def test_verifier_checks_action(self):
        """Verifier should reject mismatched action."""
        from sos.services.engine.middleware import CapabilityVerifier
        from sos.kernel.policy.capability import CapabilityModel

        verifier = CapabilityVerifier()

        cap = CapabilityModel(
            id="cap_test",
            subject="agent:test",
            action="memory:read",
            resource="memory:*",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            issuer="test"
        )

        result = verifier.verify(cap, "memory:write", "memory:test")  # Wrong action
        assert result is False

    def test_verifier_allows_wildcard_action(self):
        """Verifier should allow wildcard action."""
        from sos.services.engine.middleware import CapabilityVerifier
        from sos.kernel.policy.capability import CapabilityModel

        verifier = CapabilityVerifier()

        cap = CapabilityModel(
            id="cap_test",
            subject="agent:test",
            action="*",  # Wildcard
            resource="memory:*",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            issuer="test"
        )

        result = verifier.verify(cap, "memory:write", "memory:test")
        assert result is True

    def test_verifier_valid_capability(self):
        """Verifier should accept valid capability."""
        from sos.services.engine.middleware import CapabilityVerifier
        from sos.kernel.policy.capability import CapabilityModel

        verifier = CapabilityVerifier()

        cap = CapabilityModel(
            id="cap_test",
            subject="agent:test",
            action="memory:read",
            resource="memory:*",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            issuer="test"
        )

        result = verifier.verify(cap, "memory:read", "memory:test")
        assert result is True
