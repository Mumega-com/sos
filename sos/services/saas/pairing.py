"""Agent pairing endpoints.

A new agent proves control of an ed25519 keypair by:
  1. GET  /sos/pairing/nonce?agent_name=<name>  -> receives a short-lived nonce
  2. Signs the nonce with its private ed25519 key
  3. POST /sos/pairing with {pubkey, nonce, signature, ...}  -> receives a bearer token

The bearer token is registered in the shared tokens.json (hash-only, never plaintext),
matching the pattern used by `sos/services/saas/app.py::_register_bus_token_with_label`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException

from sos.contracts.pairing import PairingRequest, PairingResponse

log = logging.getLogger("sos.saas.pairing")

router = APIRouter(tags=["pairing"])

NONCE_TTL_SECONDS = 300           # 5 min
PAIRING_TOKEN_TTL_DAYS = 365      # 1 yr

# nonce -> (agent_name, issued_at_epoch)
# In-process store. In production this should be Redis with TTL, but the saas
# service currently runs single-process so this is safe for now. Tests clear it
# between runs via the `_NONCE_STORE` export.
_NONCE_STORE: dict[str, tuple[str, float]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ed25519_pubkey_from_str(pubkey: str) -> Ed25519PublicKey:
    """Parse an `ed25519:<base64>` public-key string into a key object."""
    if not pubkey.startswith("ed25519:"):
        raise ValueError("pubkey must start with ed25519:")
    raw = base64.b64decode(pubkey[len("ed25519:"):])
    if len(raw) != 32:
        raise ValueError(f"ed25519 pubkey must be 32 bytes, got {len(raw)}")
    return Ed25519PublicKey.from_public_bytes(raw)


def _verify_signature(pubkey: str, nonce: str, signature: str) -> bool:
    """Verify an ed25519 signature over the nonce bytes. Returns False on any error."""
    try:
        key = _ed25519_pubkey_from_str(pubkey)
        if not signature.startswith("ed25519:"):
            return False
        raw_sig = base64.b64decode(signature[len("ed25519:"):])
        key.verify(raw_sig, nonce.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def _purge_expired_nonces() -> None:
    """Drop any nonces older than NONCE_TTL_SECONDS. Best-effort."""
    cutoff = datetime.now(timezone.utc).timestamp() - NONCE_TTL_SECONDS
    stale = [n for n, (_, ts) in _NONCE_STORE.items() if ts < cutoff]
    for n in stale:
        _NONCE_STORE.pop(n, None)


def _valid_agent_name(name: str) -> bool:
    """Match the PairingRequest.agent_name pattern: ^[a-z][a-z0-9-]*$, 2..64 chars."""
    if not name or len(name) < 2 or len(name) > 64:
        return False
    if not name[0].isalpha() or not name[0].islower():
        return False
    for ch in name:
        if not (ch.islower() or ch.isdigit() or ch == "-"):
            return False
    return True


@router.get("/sos/pairing/nonce")
def issue_nonce(agent_name: str) -> dict:
    """Issue a fresh nonce bound to the requesting agent_name."""
    if not _valid_agent_name(agent_name):
        raise HTTPException(400, detail="invalid agent_name")
    _purge_expired_nonces()
    nonce = secrets.token_hex(12)  # 24-char hex, satisfies PairingRequest.nonce min 16
    _NONCE_STORE[nonce] = (agent_name, datetime.now(timezone.utc).timestamp())
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=NONCE_TTL_SECONDS)
    ).isoformat()
    return {"nonce": nonce, "expires_at": expires_at}


@router.post("/sos/pairing", response_model=PairingResponse)
def accept_pairing(req: PairingRequest) -> PairingResponse:
    """Accept a signed pairing request and mint a bearer token."""
    _purge_expired_nonces()

    # 1. Look up nonce; must have been issued for this agent_name.
    entry = _NONCE_STORE.pop(req.nonce, None)
    if entry is None or entry[0] != req.agent_name:
        raise HTTPException(
            400, detail="nonce unknown, expired, or not for this agent"
        )

    # 2. Verify signature over the nonce bytes.
    if not _verify_signature(req.pubkey, req.nonce, req.signature):
        raise HTTPException(401, detail="signature verification failed")

    # 3. Mint a bearer token (64 hex chars satisfies PairingResponse.token pattern).
    token_plain = secrets.token_hex(32)

    # 4. Register in tokens.json — hash only, never plaintext.
    _register_pairing_token(
        agent_name=req.agent_name,
        token=token_plain,
        skills=list(req.skills),
        model_provider=req.model_provider,
        role=req.role,
    )

    # 5. Build response.
    agent_id = _mint_agent_id(req.agent_name)
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(days=PAIRING_TOKEN_TTL_DAYS)

    return PairingResponse(
        token=token_plain,
        agent_id=agent_id,
        issued_at=issued.isoformat(),
        expires_at=expires.isoformat(),
        scope="agent",
    )


def _tokens_path() -> Path:
    """Return the tokens.json path, respecting SOS_TOKENS_PATH for tests."""
    env = os.environ.get("SOS_TOKENS_PATH")
    if env:
        return Path(env)
    return Path.home() / "SOS" / "sos" / "bus" / "tokens.json"


def _load_tokens(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
    except Exception:
        log.warning("tokens.json unreadable at %s; starting fresh", path)
    return []


def _mint_agent_id(agent_name: str) -> str:
    """Deterministic canonical id: `<Name>_sos_###` where ### is 1 + existing count."""
    path = _tokens_path()
    tokens = _load_tokens(path)
    count = sum(
        1 for t in tokens if (t.get("agent") or "").lower() == agent_name.lower()
    )
    # The existing entry we're about to append will bump the count; since
    # _register_pairing_token is called first in the happy path, the count
    # already reflects the new row. Use that value directly.
    return f"{agent_name.capitalize()}_sos_{count:03d}"


def _register_pairing_token(
    agent_name: str,
    token: str,
    skills: list[str],
    model_provider: str,
    role: str,
) -> None:
    """Append a paired-agent entry to tokens.json. Raw token is never stored."""
    path = _tokens_path()
    tokens = _load_tokens(path)
    tokens.append({
        "token": "",  # plaintext never stored
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "agent": agent_name,
        "label": f"{agent_name}: paired ({model_provider})",
        "scope": "agent",
        "active": True,
        "created_at": _now_iso(),
        "role": role,
        "skills": skills,
        "model_provider": model_provider,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2))
    log.info(
        "Paired agent %s (role=%s, provider=%s, skills=%d)",
        agent_name,
        role,
        model_provider,
        len(skills),
    )
