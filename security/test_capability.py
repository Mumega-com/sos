from sos.security.capability import Capability, CapabilityFactory
from nacl.signing import SigningKey
from datetime import datetime, timezone, timedelta
import time

def test_capability_lifecycle():
    print("Testing Capability Lifecycle...")
    
    # 1. Setup Keys
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    
    # hex strings for storage
    private_key_hex = signing_key.encode().hex()
    public_key_hex = verify_key.encode().hex()
    
    print(f"Generated Issuer Keypair.")
    
    # 2. Create Factory
    factory = CapabilityFactory(issuer_id="river-test", signing_key_hex=private_key_hex)
    
    # 3. Create Capability
    cap = factory.create(
        subject="agent:dandan",
        action="memory:read",
        resource="memory:agent:dandan/*",
        constraints={"limit": 10},
        ttl_seconds=60
    )
    
    print(f"Created Capability: {cap.id}")
    
    # 4. Verification (Should Pass)
    is_valid = cap.verify(verify_key)
    print(f"Verification (Fresh): {'✅ PASS' if is_valid else '❌ FAIL'}")
    
    # 5. Tampering (Should Fail)
    cap.resource = "memory:all" # Malicious modification
    is_valid_tampered = cap.verify(verify_key)
    print(f"Verification (Tampered): {'✅ PASS' if not is_valid_tampered else '❌ FAIL'}")
    
    # Reset resource
    cap.resource = "memory:agent:dandan/*"
    
    # 6. Serialization (Token)
    token = cap.to_token()
    print(f"Serialized Token: {token[:20]}...")
    
    # 7. Deserialization
    rehydrated_cap = Capability.from_token(token)
    print(f"Rehydrated ID: {rehydrated_cap.id}")
    
    is_valid_rehydrated = rehydrated_cap.verify(verify_key)
    print(f"Verification (Rehydrated): {'✅ PASS' if is_valid_rehydrated else '❌ FAIL'}")
    
    # 8. Expiration (Mocking time check)
    # We'll create a short-lived capability manually for this
    expired_cap = factory.create(
        subject="agent:dandan", 
        action="test", 
        resource="test", 
        ttl_seconds=-10 # Expired 10 seconds ago
    )
    is_valid_expired = expired_cap.verify(verify_key)
    print(f"Verification (Expired): {'✅ PASS' if not is_valid_expired else '❌ FAIL'}")

if __name__ == "__main__":
    test_capability_lifecycle()
