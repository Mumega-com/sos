"""Unit tests for sos.kernel.crypto — Ed25519 sign/verify + canonical hashing."""

from __future__ import annotations

import pytest

from sos.kernel.crypto import (
    canonical_payload_hash,
    enroll_message,
    generate_keypair,
    sign,
    verify,
)


def test_keypair_roundtrip():
    priv, pub = generate_keypair()
    assert len(priv) > 0 and len(pub) > 0
    assert priv != pub
    msg = b"hello mesh"
    sig = sign(priv, msg)
    assert verify(pub, msg, sig) is True


def test_verify_rejects_wrong_key():
    priv_a, pub_a = generate_keypair()
    _priv_b, pub_b = generate_keypair()
    sig = sign(priv_a, b"claim")
    assert verify(pub_a, b"claim", sig) is True
    assert verify(pub_b, b"claim", sig) is False


def test_verify_rejects_tampered_message():
    priv, pub = generate_keypair()
    sig = sign(priv, b"original")
    assert verify(pub, b"tampered", sig) is False


def test_verify_rejects_malformed_inputs():
    priv, pub = generate_keypair()
    sig = sign(priv, b"msg")
    assert verify("not-base64!", b"msg", sig) is False
    assert verify(pub, b"msg", "not-base64!") is False
    assert verify("", b"msg", sig) is False


def test_canonical_payload_hash_order_independent():
    a = canonical_payload_hash({"b": 2, "a": 1})
    b = canonical_payload_hash({"a": 1, "b": 2})
    assert a == b


def test_canonical_payload_hash_sensitive_to_value_change():
    a = canonical_payload_hash({"a": 1})
    b = canonical_payload_hash({"a": 2})
    assert a != b


def test_enroll_message_shape():
    msg = enroll_message("agent:foo", "n0nce", "deadbeef")
    assert msg == b"agent:foo|n0nce|deadbeef"


def test_end_to_end_enroll_signature():
    """Full /mesh/enroll signing flow — the path the registry will run."""
    priv, pub = generate_keypair()
    payload = {
        "agent_id": "agent:hermes",
        "name": "hermes",
        "role": "specialist",
        "skills": ["acp", "orchestration"],
        "squads": ["mesh"],
    }
    nonce = "server-issued-nonce-xyz"
    h = canonical_payload_hash(payload)
    msg = enroll_message(payload["agent_id"], nonce, h)
    sig = sign(priv, msg)

    # Server side: same computation, verify with stored public key.
    server_msg = enroll_message(payload["agent_id"], nonce, canonical_payload_hash(payload))
    assert server_msg == msg
    assert verify(pub, server_msg, sig) is True

    # Drift any field in payload → hash changes → signature invalid.
    bad_payload = dict(payload, role="coordinator")
    bad_msg = enroll_message(payload["agent_id"], nonce, canonical_payload_hash(bad_payload))
    assert verify(pub, bad_msg, sig) is False
