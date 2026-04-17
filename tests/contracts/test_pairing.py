"""Contract tests for the Agent Pairing schema + Pydantic models.

These tests are the freeze point: if they pass, any implementation
(Python, Rust, TypeScript) that emits PairingRequest / PairingResponse
records passing them is wire-compatible.
"""
from __future__ import annotations

import pytest

from sos.contracts.pairing import PairingRequest, PairingResponse, load_schema


# Base64-ish strings chosen to satisfy the ed25519:<40-88/128 char> pattern.
_VALID_PUBKEY = "ed25519:" + "A" * 43
_VALID_SIGNATURE = "ed25519:" + "B" * 64
_VALID_TOKEN = "a" * 64  # 64 hex chars
_VALID_NONCE = "n" * 32
_VALID_ISSUED_AT = "2026-04-17T20:00:00Z"
_VALID_EXPIRES_AT = "2027-04-17T20:00:00Z"


def _valid_request_kwargs() -> dict:
    return {
        "agent_name": "hermes",
        "pubkey": _VALID_PUBKEY,
        "skills": ["routing", "signing"],
        "model_provider": "anthropic:claude-opus-4-7",
        "nonce": _VALID_NONCE,
        "signature": _VALID_SIGNATURE,
    }


def _valid_response_kwargs() -> dict:
    return {
        "token": _VALID_TOKEN,
        "agent_id": "Hermes_sos_007",
        "issued_at": _VALID_ISSUED_AT,
        "expires_at": _VALID_EXPIRES_AT,
    }


# ---- PairingRequest -------------------------------------------------------

def test_minimal_valid_request_instantiates():
    """A minimal PairingRequest with all six required fields validates and role defaults to 'specialist'."""
    req = PairingRequest(**_valid_request_kwargs())
    assert req.agent_name == "hermes"
    assert req.role == "specialist"


def test_agent_name_pattern_validated():
    """agent_name with uppercase letters must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "agent_name": "Hermes"})


def test_agent_name_min_length():
    """agent_name shorter than min_length=2 must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "agent_name": "a"})


def test_pubkey_must_have_ed25519_prefix():
    """pubkey without the 'ed25519:' prefix must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "pubkey": "A" * 64})


def test_skills_must_not_be_empty():
    """skills=[] must be rejected by the validator."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "skills": []})


def test_skills_must_be_unique():
    """duplicate entries in skills must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "skills": ["x", "x"]})


def test_skills_pattern_validated():
    """skill slugs that violate ^[a-z][a-z0-9-]*$ must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "skills": ["BadSkill"]})


def test_model_provider_pattern_validated():
    """model_provider without the required colon segment must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "model_provider": "anthropic"})


def test_nonce_min_length():
    """nonce shorter than min_length=16 must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "nonce": "short"})


def test_signature_pattern_validated():
    """signature without the ed25519: prefix must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "signature": "notsigned"})


def test_role_enum_rejects_unknown():
    """role outside the Literal enum must be rejected."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "role": "overlord"})


def test_request_extra_forbidden():
    """extra fields must be rejected (extra='forbid')."""
    with pytest.raises(ValueError):
        PairingRequest(**{**_valid_request_kwargs(), "unexpected": "field"})


# ---- PairingResponse ------------------------------------------------------

def test_minimal_valid_response_instantiates():
    """A minimal PairingResponse with all four required fields validates and scope defaults to 'agent'."""
    resp = PairingResponse(**_valid_response_kwargs())
    assert resp.token == _VALID_TOKEN
    assert resp.scope == "agent"


def test_token_must_be_64_hex():
    """token that is not 64 lowercase hex characters must be rejected."""
    with pytest.raises(ValueError):
        PairingResponse(**{**_valid_response_kwargs(), "token": "xyz"})


def test_agent_id_pattern_validated():
    """agent_id starting with a digit must be rejected."""
    with pytest.raises(ValueError):
        PairingResponse(**{**_valid_response_kwargs(), "agent_id": "7bad"})


def test_issued_at_iso():
    """issued_at must parse as ISO-8601 date-time."""
    with pytest.raises(ValueError):
        PairingResponse(**{**_valid_response_kwargs(), "issued_at": "yesterday"})


def test_expires_at_iso():
    """expires_at must parse as ISO-8601 date-time."""
    with pytest.raises(ValueError):
        PairingResponse(**{**_valid_response_kwargs(), "expires_at": "yesterday"})


def test_scope_enum_rejects_unknown():
    """scope outside the Literal enum must be rejected."""
    with pytest.raises(ValueError):
        PairingResponse(**{**_valid_response_kwargs(), "scope": "god"})


# ---- Schema ---------------------------------------------------------------

def test_schema_file_parses():
    """The pairing JSON Schema must itself meta-validate against Draft 2020-12."""
    from jsonschema import Draft202012Validator

    schema = load_schema()
    Draft202012Validator.check_schema(schema)


def test_load_schema_contains_defs():
    """load_schema() must expose $defs for PairingRequest and PairingResponse."""
    schema = load_schema()
    assert "$defs" in schema
    assert "PairingRequest" in schema["$defs"]
    assert "PairingResponse" in schema["$defs"]
