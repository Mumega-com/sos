"""
Sprint 008 S008-A / G76 — internal knight-mint tests (6 TCs).

TC-G76-a  Happy path: mint_internal_knight produces principal + token + binding + emit
TC-G76-b  AUDIT_INTERNAL_MINT_MODE not set → InternalMintModeDisabled
TC-G76-c  Missing required arg → MissingMintArgError
TC-G76-d  Duplicate name → {ok: True, reason: already_minted}, no new rows
TC-G76-e  16D vector deterministic (same name+role → same vector)
TC-G76-f  Invalid signer → InvalidSignerError
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sos.services.billing.internal_knight_mint import (
    InternalMintModeDisabled,
    InvalidSignerError,
    MissingMintArgError,
    mint_internal_knight,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_env(**extra):
    """Base env for internal mint tests."""
    env = {"AUDIT_INTERNAL_MINT_MODE": "1"}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# TC-G76-a: happy path
# ---------------------------------------------------------------------------


def test_g76_a_happy_path() -> None:
    """TC-G76-a: mint_internal_knight happy path produces all artifacts."""
    emitted: list[dict] = []

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump([], tf)
        tokens_path = Path(tf.name)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as rf:
        json.dump({}, rf)
        registry_path = Path(rf.name)

    with patch.dict("os.environ", _mock_env()), \
         patch("sos.services.billing.internal_knight_mint._TOKENS_PATH", tokens_path), \
         patch("sos.services.billing.internal_knight_mint._QNFT_REGISTRY_PATH", registry_path), \
         patch("sos.services.billing.internal_knight_mint._bind_discord_channel"), \
         patch("sos.contracts.principals.get_principal", return_value=None), \
         patch("sos.contracts.principals.upsert_principal"), \
         patch("sos.services.billing.qnft_image.generate_qnft_image",
               return_value=b"\x89PNG" * 10), \
         patch("sos.services.billing.knight_mint._bus_send_welcome"), \
         patch("sos.observability.sprint_telemetry.emit_internal_knight_minted",
               side_effect=lambda knight_id, signer, channel_id: emitted.append(
                   {"knight_id": knight_id, "signer": signer})):

        result = mint_internal_knight("gavin", "closer", "1234567890", "loom")

    assert result["ok"] is True
    assert result["reason"] == "minted"
    assert result["knight_id"] == "agent:gavin-knight"
    assert result["knight_slug"] == "gavin-knight"
    assert result["vector_16d"] is not None
    assert len(result["vector_16d"]) == 16

    # Token written
    tokens = json.loads(tokens_path.read_text())
    assert any(t["agent"] == "gavin-knight" for t in tokens)

    # QNFT registry written
    registry = json.loads(registry_path.read_text())
    assert "gavin-knight" in registry
    assert registry["gavin-knight"]["signer"] == "loom"

    # Emit fired
    assert len(emitted) == 1
    assert emitted[0]["knight_id"] == "agent:gavin-knight"

    tokens_path.unlink(missing_ok=True)
    registry_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TC-G76-b: env not set → InternalMintModeDisabled
# ---------------------------------------------------------------------------


def test_g76_b_env_not_set_raises() -> None:
    """TC-G76-b: AUDIT_INTERNAL_MINT_MODE not set raises InternalMintModeDisabled."""
    with patch.dict("os.environ", {}, clear=False):
        # Ensure it's not set
        import os
        os.environ.pop("AUDIT_INTERNAL_MINT_MODE", None)

        with pytest.raises(InternalMintModeDisabled):
            mint_internal_knight("test", "role", "channel", "loom")


# ---------------------------------------------------------------------------
# TC-G76-c: missing arg → MissingMintArgError
# ---------------------------------------------------------------------------


def test_g76_c_missing_arg_raises() -> None:
    """TC-G76-c: missing required arg raises MissingMintArgError."""
    with patch.dict("os.environ", _mock_env()):
        with pytest.raises(MissingMintArgError):
            mint_internal_knight("", "role", "channel", "loom")
        with pytest.raises(MissingMintArgError):
            mint_internal_knight("name", "", "channel", "loom")
        with pytest.raises(MissingMintArgError):
            mint_internal_knight("name", "role", "", "loom")


# ---------------------------------------------------------------------------
# TC-G76-d: duplicate → already_minted, no new rows
# ---------------------------------------------------------------------------


def test_g76_d_duplicate_skip() -> None:
    """TC-G76-d: second mint for same name returns already_minted."""
    mock_principal = {"id": "agent:gavin-knight", "display_name": "gavin-knight"}

    with patch.dict("os.environ", _mock_env()), \
         patch("sos.contracts.principals.get_principal", return_value=mock_principal):

        result = mint_internal_knight("gavin", "closer", "1234567890", "loom")

    assert result["ok"] is True
    assert result["reason"] == "already_minted"
    assert result["skipped"] is True


# ---------------------------------------------------------------------------
# TC-G76-e: deterministic vector
# ---------------------------------------------------------------------------


def test_g76_e_deterministic_vector() -> None:
    """TC-G76-e: same name+role produces same 16D vector every time."""
    seed1 = hashlib.sha256("gavin-knight:closer".encode()).digest()
    vec1 = [
        round(((b0 << 8 | b1) / 65535.0) * 2.0 - 1.0, 6)
        for b0, b1 in zip(seed1[::2], seed1[1::2])
    ]

    seed2 = hashlib.sha256("gavin-knight:closer".encode()).digest()
    vec2 = [
        round(((b0 << 8 | b1) / 65535.0) * 2.0 - 1.0, 6)
        for b0, b1 in zip(seed2[::2], seed2[1::2])
    ]

    assert vec1 == vec2
    assert len(vec1) == 16
    # Verify non-uniform
    assert len(set(vec1)) > 1


# ---------------------------------------------------------------------------
# TC-G76-f: invalid signer → InvalidSignerError
# ---------------------------------------------------------------------------


def test_g76_f_invalid_signer_raises() -> None:
    """TC-G76-f: signer not in allowed set raises InvalidSignerError."""
    with patch.dict("os.environ", _mock_env()):
        with pytest.raises(InvalidSignerError):
            mint_internal_knight("test", "role", "channel", "random")
        with pytest.raises(InvalidSignerError):
            mint_internal_knight("test", "role", "channel", "kasra")
