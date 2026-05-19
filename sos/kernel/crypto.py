"""Ed25519 sign/verify helpers for the SOS mesh.

Thin wrapper over PyNaCl. All keys and signatures are URL-safe base64
strings (no padding) so they round-trip through HTTP JSON and Redis
hashes without escaping.

Used by /mesh/enroll to verify that the caller holds the private key
matching the AgentIdentity's stored public_key. The signature binds
(agent_id, nonce, payload_hash) — see docs/plans/2026-04-19-mesh-security-wave.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) — both URL-safe base64, no padding."""
    sk = SigningKey.generate()
    return _b64e(bytes(sk)), _b64e(bytes(sk.verify_key))


def sign(private_key_b64: str, message: bytes) -> str:
    """Return base64 Ed25519 signature over *message*."""
    sk = SigningKey(_b64d(private_key_b64))
    return _b64e(sk.sign(message).signature)


def verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    """Return True iff *signature_b64* is a valid Ed25519 signature over *message*."""
    try:
        vk = VerifyKey(_b64d(public_key_b64))
        vk.verify(message, _b64d(signature_b64))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Return SHA-256 hex of the JSON-canonical form of *payload*.

    Sorted keys + no whitespace so sender and verifier compute the same hash
    from the same field set. Used to bind the enroll body to the signature.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def enroll_message(agent_id: str, nonce: str, payload_hash: str) -> bytes:
    """Return the exact byte string that must be signed for /mesh/enroll.

    Server and client both compute this the same way — any drift = invalid
    signature. Keep the format simple and pipe-delimited; changing it is a
    protocol break.
    """
    return f"{agent_id}|{nonce}|{payload_hash}".encode("utf-8")


def public_key_from_private(private_key_b64: str) -> str:
    """Derive the base64 Ed25519 public key from its private (signing) key."""
    sk = SigningKey(_b64d(private_key_b64))
    return _b64e(bytes(sk.verify_key))


def load_or_create_keypair(agent_id: str) -> tuple[str, str]:
    """Return ``(private_b64, public_b64)`` from disk, generating if missing.

    Keys live at ``$SOS_KEYS_DIR/<safe_agent_id>.priv`` (default
    ``~/.sos/keys/``). The directory is created with mode 0700 and files
    with mode 0600 so other users on the host can't read them.

    ``agent_id`` accepts either ``agent:hermes`` or plain ``hermes``; the
    ``agent:`` prefix is stripped for the filename.
    """
    import os
    import stat
    from pathlib import Path

    safe = agent_id.removeprefix("agent:")
    keys_dir = Path(os.environ.get("SOS_KEYS_DIR", Path.home() / ".sos" / "keys"))
    keys_dir.mkdir(parents=True, exist_ok=True)
    try:
        keys_dir.chmod(stat.S_IRWXU)  # 0700
    except (OSError, PermissionError):
        pass

    priv_path = keys_dir / f"{safe}.priv"
    pub_path = keys_dir / f"{safe}.pub"

    if priv_path.exists():
        priv_b64 = priv_path.read_text().strip()
        pub_b64 = public_key_from_private(priv_b64)
        return priv_b64, pub_b64

    priv_b64, pub_b64 = generate_keypair()
    priv_path.write_text(priv_b64)
    pub_path.write_text(pub_b64)
    try:
        priv_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except (OSError, PermissionError):
        pass
    return priv_b64, pub_b64
