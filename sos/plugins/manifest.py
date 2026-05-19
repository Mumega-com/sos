from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Optional, Tuple

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey
from pydantic import BaseModel, Field


TrustLevel = Literal["core", "verified", "community", "unsigned"]


class PluginManifest(BaseModel):
    name: str
    version: str
    author: str
    description: str = ""
    trust_level: TrustLevel = "unsigned"
    capabilities_required: List[str] = Field(default_factory=list)
    capabilities_provided: List[str] = Field(default_factory=list)
    entrypoints: Dict[str, str] = Field(default_factory=dict)
    sandbox: Dict[str, Any] = Field(default_factory=dict)
    signature: Optional[str] = None

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(exclude={"signature"}, mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def signing_hash(self) -> bytes:
        return hashlib.sha256(self.signing_bytes()).digest()


def sign_plugin_manifest(manifest: PluginManifest, signing_key: SigningKey) -> str:
    """
    Sign a plugin manifest using Ed25519 and store the signature on the manifest.

    Signature format: "ed25519:<hex>"
    Message: sha256(canonical_json(manifest_without_signature))
    """
    message = manifest.signing_hash()
    signature = signing_key.sign(message).signature.hex()
    manifest.signature = f"ed25519:{signature}"
    return manifest.signature


def _parse_ed25519_signature(signature: str) -> bytes:
    signature = signature.strip()
    if signature.startswith("ed25519:"):
        signature = signature.split(":", 1)[1]
    return bytes.fromhex(signature)


def verify_plugin_manifest_signature(manifest: PluginManifest, public_key: bytes) -> Tuple[bool, str]:
    """
    Verify a plugin manifest's signature against the provided public key.

    Message: sha256(canonical_json(manifest_without_signature))
    """
    if not manifest.signature:
        return False, "Plugin manifest is missing signature"

    try:
        signature_bytes = _parse_ed25519_signature(manifest.signature)
    except ValueError:
        return False, "Invalid signature encoding"

    verify_key = VerifyKey(public_key)
    message = manifest.signing_hash()
    try:
        verify_key.verify(message, signature_bytes)
        return True, "Valid"
    except BadSignatureError:
        return False, "Invalid signature"

