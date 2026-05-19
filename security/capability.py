from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Optional, Any
import json
import base64
import uuid
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError

@dataclass
class Capability:
    """
    An unforgeable token granting specific permissions to an agent or service.
    Follows the SovereignOS Security Model v0.1.
    """
    id: str                    # Unique capability ID
    subject: str               # Agent or service ID (e.g., "agent:kasra")
    action: str                # Action being permitted (e.g., "memory:read")
    resource: str              # Resource pattern (e.g., "memory:agent:kasra/*")
    constraints: Dict[str, Any]# Additional constraints (e.g., {"max_results": 100})
    issued_at: datetime        # ISO 8601 timestamp
    expires_at: datetime       # ISO 8601 timestamp
    issuer: str                # ID of the issuing authority (e.g., "river")
    signature: str = ""        # Ed25519 signature (hex encoded)

    def to_dict(self) -> Dict[str, Any]:
        """Convert capability to dictionary for serialization."""
        data = asdict(self)
        # Convert datetimes to ISO strings
        data['issued_at'] = self.issued_at.isoformat()
        data['expires_at'] = self.expires_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Capability':
        """Create capability from dictionary."""
        # Convert ISO strings back to datetimes
        data['issued_at'] = datetime.fromisoformat(data['issued_at'])
        data['expires_at'] = datetime.fromisoformat(data['expires_at'])
        return cls(**data)

    def _get_signable_payload(self) -> bytes:
        """
        Create a canonical byte representation of the capability for signing.
        Excludes the signature itself.
        """
        payload = {
            "id": self.id,
            "subject": self.subject,
            "action": self.action,
            "resource": self.resource,
            "constraints": self.constraints,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "issuer": self.issuer,
        }
        # Sort keys to ensure deterministic serialization
        return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')

    def sign(self, signing_key: SigningKey) -> None:
        """
        Sign the capability using the provided Ed25519 signing key.
        The signature is stored in the `signature` field.
        """
        message = self._get_signable_payload()
        signed = signing_key.sign(message)
        # Store just the signature part, hex encoded
        self.signature = signed.signature.hex()

    def verify(self, verify_key: VerifyKey) -> bool:
        """
        Verify the capability's signature and expiration.
        Returns True if valid, False otherwise.
        """
        # 1. Check expiration
        now = datetime.now(timezone.utc)
        if now > self.expires_at:
            return False

        # 2. Verify signature
        try:
            message = self._get_signable_payload()
            signature_bytes = bytes.fromhex(self.signature)
            verify_key.verify(message, signature_bytes)
            return True
        except (BadSignatureError, ValueError):
            return False

    def to_token(self) -> str:
        """
        Serialize to a base64url-encoded token string suitable for HTTP headers.
        """
        json_str = json.dumps(self.to_dict())
        return base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')

    @classmethod
    def from_token(cls, token: str) -> 'Capability':
        """
        Deserialize from a base64url-encoded token string.
        """
        try:
            # Add padding if missing
            missing_padding = len(token) % 4
            if missing_padding:
                token += '=' * (4 - missing_padding)
            
            json_str = base64.urlsafe_b64decode(token).decode('utf-8')
            data = json.loads(json_str)
            return cls.from_dict(data)
        except Exception as e:
            raise ValueError(f"Invalid capability token: {str(e)}")

# Factory for creating new capabilities
class CapabilityFactory:
    def __init__(self, issuer_id: str, signing_key_hex: str):
        self.issuer_id = issuer_id
        self.signing_key = SigningKey(bytes.fromhex(signing_key_hex))

    def create(self, subject: str, action: str, resource: str, constraints: Optional[Dict] = None, ttl_seconds: int = 3600) -> Capability:
        now = datetime.now(timezone.utc)
        expires = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc)
        
        cap = Capability(
            id=str(uuid.uuid4()),
            subject=subject,
            action=action,
            resource=resource,
            constraints=constraints or {},
            issued_at=now,
            expires_at=expires,
            issuer=self.issuer_id
        )
        cap.sign(self.signing_key)
        return cap

    def get_verify_key(self) -> VerifyKey:
        return self.signing_key.verify_key
