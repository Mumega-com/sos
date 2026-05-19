"""
S028 B2 Phase 1 — LOCK-S028-B-1 L-3 audit log additive tests.

Verifies `_audit_emit` writes structured records to the
`sos:audit:bridge:v1` Redis stream with the expected fields, and that
audit failures never raise (defense-in-depth — Phase 1 is observation,
not enforcement).

Phase 1 is pure observation; behavioral tests on the 5 endpoints belong
to a future integration harness once the rate-limiter middleware (Phase 2)
and identity-binding gate (Phase 3) are wired in.

Hermetic discipline: monkeypatches the module-level `r` symbol on
`sos.bus.bridge` to a FakeRedis stub so tests run without a live Redis.
"""
from __future__ import annotations

import pytest

from sos.bus import bridge


class _FakeRedis:
    """Minimal fake honoring just the XADD shape used by `_audit_emit`."""

    def __init__(self) -> None:
        self.streams: dict[str, list[dict]] = {}
        self.raise_on_xadd = False

    def xadd(self, stream: str, fields: dict, maxlen: int | None = None,
             approximate: bool = False) -> str:
        if self.raise_on_xadd:
            raise RuntimeError("simulated redis failure")
        self.streams.setdefault(stream, []).append(dict(fields))
        return f"{len(self.streams[stream])}-0"


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(bridge, "r", fake, raising=False)
    return fake


def _token(agent: str = "kasra", thash: str = "0123456789abcdef" * 4) -> dict:
    return {"agent": agent, "token_hash": thash, "active": True}


# -----------------------------------------------------------------------
# L-3: audit_emit writes to sos:audit:bridge:v1 with required fields
# -----------------------------------------------------------------------

def test_audit_emit_writes_to_canonical_stream(fake_redis):
    bridge._audit_emit(_token(), "/send", claimed="kasra", target="loom")
    assert "sos:audit:bridge:v1" in fake_redis.streams
    records = fake_redis.streams["sos:audit:bridge:v1"]
    assert len(records) == 1
    rec = records[0]
    # Required fields present
    assert rec["endpoint"] == "/send"
    assert rec["token_agent"] == "kasra"
    assert rec["claimed_agent"] == "kasra"
    assert rec["target"] == "loom"
    assert rec["token_hash_short"] == "0123456789abcdef"  # first 16 chars
    # Timestamp ISO-shaped (just verify presence + plausible length)
    assert "T" in rec["ts"]
    # Binding match: kasra==kasra → "1"
    assert rec["binding_match"] == "1"


def test_audit_emit_marks_binding_mismatch(fake_redis):
    """Phase-1 observation: surfaces caller spoofing for shadow audit."""
    bridge._audit_emit(_token(agent="kasra"), "/send", claimed="loom", target="athena")
    rec = fake_redis.streams["sos:audit:bridge:v1"][0]
    assert rec["token_agent"] == "kasra"
    assert rec["claimed_agent"] == "loom"
    assert rec["binding_match"] == "0"  # would_block_at_phase_3


def test_audit_emit_empty_when_no_claim(fake_redis):
    bridge._audit_emit(_token(agent="kasra"), "/health-like", claimed=None)
    rec = fake_redis.streams["sos:audit:bridge:v1"][0]
    assert rec["claimed_agent"] == ""
    # binding_match empty when no identity claimed
    assert rec["binding_match"] == ""


def test_audit_emit_includes_extra_fields(fake_redis):
    bridge._audit_emit(
        _token(),
        "/announce",
        claimed="kasra",
        target="kasra",
        extra={"tool": "claude-code"},
    )
    rec = fake_redis.streams["sos:audit:bridge:v1"][0]
    assert rec["tool"] == "claude-code"


# -----------------------------------------------------------------------
# Defense-in-depth: audit must NEVER raise from the call site
# -----------------------------------------------------------------------

def test_audit_emit_swallows_xadd_failure(fake_redis, capsys):
    """If Redis XADD fails, audit_emit must NOT raise — observation
    surface must not block business logic in Phase 1."""
    fake_redis.raise_on_xadd = True
    # Should not raise
    bridge._audit_emit(_token(), "/send", claimed="kasra", target="loom")
    # And should log the failure to stderr-printed line
    out = capsys.readouterr()
    assert "audit emit failed" in out.out + out.err


def test_audit_emit_handles_malformed_token_record(fake_redis):
    """Token record without `agent` field must not raise (Phase 1
    behavior; Phase 3 binding gate will reject 403 separately)."""
    bridge._audit_emit({}, "/send", claimed="loom", target="athena")
    rec = fake_redis.streams["sos:audit:bridge:v1"][0]
    assert rec["token_agent"] == ""  # empty when missing
    assert rec["claimed_agent"] == "loom"
    # binding_match empty when token_agent empty (cannot evaluate)
    assert rec["binding_match"] == ""


def test_audit_emit_token_hash_short_truncates(fake_redis):
    """token_hash_short should be exactly the first 16 chars of the
    full hash — bounds info disclosure (full hash is identity-equivalent
    to the raw token under the SHA-256 storage scheme)."""
    full_hash = "f" * 64  # 64-char SHA-256 hex
    bridge._audit_emit({"agent": "x", "token_hash": full_hash}, "/inbox", claimed="x")
    rec = fake_redis.streams["sos:audit:bridge:v1"][0]
    assert rec["token_hash_short"] == "f" * 16
    assert len(rec["token_hash_short"]) == 16


# -----------------------------------------------------------------------
# LOCK marker discoverability (lint:locks substitute)
# -----------------------------------------------------------------------

def test_lock_marker_present_in_bridge_source():
    from pathlib import Path
    src = Path(bridge.__file__).read_text()
    assert "LOCK-S028-B-1" in src  # main LOCK marker
    # _audit_emit helper docstring or comments should reference L-3 leg
    assert "L-3" in src
