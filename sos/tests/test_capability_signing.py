import unittest

from nacl.signing import SigningKey

from sos.kernel.capability import (
    Capability,
    CapabilityAction,
    sign_capability,
    verify_capability,
    verify_capability_signature,
)


class TestCapabilitySigning(unittest.TestCase):
    def test_sign_and_verify_signature(self):
        signing_key = SigningKey.generate()
        public_key = signing_key.verify_key.encode()

        cap = Capability(
            subject="agent:kasra",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:kasra/*",
            uses_remaining=3,
            parent_id="cap_parent_001",
        )

        signature = sign_capability(cap, signing_key)
        self.assertTrue(signature.startswith("ed25519:"))

        ok, reason = verify_capability_signature(cap, public_key)
        self.assertTrue(ok, reason)

    def test_signature_fails_on_payload_tamper(self):
        signing_key = SigningKey.generate()
        public_key = signing_key.verify_key.encode()

        cap = Capability(
            subject="agent:kasra",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:kasra/*",
        )
        sign_capability(cap, signing_key)

        cap.resource = "memory:other/*"
        ok, _ = verify_capability_signature(cap, public_key)
        self.assertFalse(ok)

    def test_verify_capability_requires_signature_when_public_key_provided(self):
        signing_key = SigningKey.generate()
        public_key = signing_key.verify_key.encode()

        cap = Capability(
            subject="agent:kasra",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:kasra/*",
        )

        ok, reason = verify_capability(
            capability=cap,
            required_action=CapabilityAction.MEMORY_READ,
            resource="memory:kasra/*",
            public_key=public_key,
        )
        self.assertFalse(ok)
        self.assertIn("missing signature", reason.lower())

    def test_use_does_not_mutate_uses_remaining(self):
        cap = Capability(
            subject="agent:kasra",
            action=CapabilityAction.MEMORY_READ,
            resource="memory:kasra/*",
            uses_remaining=1,
        )
        self.assertTrue(cap.use())
        self.assertEqual(cap.uses_remaining, 1)


if __name__ == "__main__":
    unittest.main()

