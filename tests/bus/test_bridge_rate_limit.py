"""
S028 B2 Phase 2 — LOCK-S028-B-1 L-1 rate-limit shadow tests.

Verifies `_rate_check` returns the correct verdict dict (allow vs
would_block) and writes per-token bucket counters to Redis. Phase 2 is
pure observation; Phase 4 will flip the same verdict surface to
enforce (429 + Retry-After).

Hermetic: monkeypatches `bridge.r` to a FakeRedis stub that honors the
INCR + EXPIRE shape used by `_rate_check`. Time is monkeypatched to a
fixed epoch so bucket math is deterministic.

Caps under test:
  default tokens:                60 req/min
  rate_limit_class="elevated":  600 req/min
"""
from __future__ import annotations

import pytest

from sos.bus import bridge


class _FakeRedis:
    """Minimal fake honoring INCR / EXPIRE / XADD shapes used here."""

    def __init__(self) -> None:
        self.kv: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.streams: dict[str, list[dict]] = {}
        self.raise_on_incr = False

    def incr(self, key: str) -> int:
        if self.raise_on_incr:
            raise RuntimeError("simulated redis failure")
        self.kv[key] = self.kv.get(key, 0) + 1
        return self.kv[key]

    def expire(self, key: str, ttl: int) -> bool:
        self.ttls[key] = ttl
        return True

    def xadd(self, stream: str, fields: dict, maxlen: int | None = None,
             approximate: bool = False) -> str:
        self.streams.setdefault(stream, []).append(dict(fields))
        return f"{len(self.streams[stream])}-0"


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(bridge, "r", fake, raising=False)
    return fake


@pytest.fixture
def fixed_time(monkeypatch):
    # Pin epoch so bucket = 12345 is deterministic across the test
    fixed_epoch = 12345 * bridge.RATE_LIMIT_WINDOW_SEC + 7  # offset within bucket
    monkeypatch.setattr(bridge.time, "time", lambda: fixed_epoch)
    return fixed_epoch


def _token(*, hash_: str = "f" * 64, rate_class: str | None = None) -> dict:
    t: dict = {"agent": "kasra", "token_hash": hash_, "active": True}
    if rate_class is not None:
        t["rate_limit_class"] = rate_class
    return t


# -----------------------------------------------------------------------
# Default cap (60/min) — allow then would_block
# -----------------------------------------------------------------------

def test_rate_check_allows_under_default_limit(fake_redis, fixed_time):
    verdict = bridge._rate_check(_token(), "/send")
    assert verdict["rate_verdict"] == "allow"
    assert verdict["rate_count"] == "1"
    assert verdict["rate_limit"] == str(bridge.RATE_LIMIT_DEFAULT)
    assert verdict["rate_endpoint"] == "/send"


def test_rate_check_blocks_when_default_limit_exceeded(fake_redis, fixed_time):
    tok = _token()
    # First 60 calls allow; 61st would_block.
    for i in range(bridge.RATE_LIMIT_DEFAULT):
        v = bridge._rate_check(tok, "/send")
        assert v["rate_verdict"] == "allow", f"call {i + 1} should allow"
    v = bridge._rate_check(tok, "/send")
    assert v["rate_verdict"] == "would_block"
    assert v["rate_count"] == str(bridge.RATE_LIMIT_DEFAULT + 1)


def test_rate_check_count_at_limit_still_allowed(fake_redis, fixed_time):
    """Boundary: count == limit is the last allowed; count > limit blocks."""
    tok = _token()
    for i in range(bridge.RATE_LIMIT_DEFAULT - 1):
        bridge._rate_check(tok, "/send")
    v = bridge._rate_check(tok, "/send")  # count == limit
    assert v["rate_verdict"] == "allow"
    assert v["rate_count"] == str(bridge.RATE_LIMIT_DEFAULT)


# -----------------------------------------------------------------------
# Elevated cap (600/min)
# -----------------------------------------------------------------------

def test_rate_check_uses_elevated_limit(fake_redis, fixed_time):
    tok = _token(rate_class="elevated")
    v = bridge._rate_check(tok, "/broadcast")
    assert v["rate_limit"] == str(bridge.RATE_LIMIT_ELEVATED)
    assert v["rate_verdict"] == "allow"


def test_rate_check_elevated_blocks_at_higher_threshold(fake_redis, fixed_time):
    tok = _token(rate_class="elevated")
    # 60 calls — would block default token but elevated still allows
    for _ in range(bridge.RATE_LIMIT_DEFAULT + 5):
        v = bridge._rate_check(tok, "/broadcast")
        assert v["rate_verdict"] == "allow"


def test_rate_class_unknown_value_falls_back_to_default(fake_redis, fixed_time):
    """Unknown rate_class string must NOT silently elevate. Fail-closed
    posture for capacity — only the literal "elevated" string opens the
    higher cap. Anything else (typos, attacker-supplied tampering at token
    mint) treated as default."""
    tok = _token(rate_class="ELEVATED")  # case mismatch
    v = bridge._rate_check(tok, "/send")
    assert v["rate_limit"] == str(bridge.RATE_LIMIT_DEFAULT)


# -----------------------------------------------------------------------
# Bucket key shape + EXPIRE semantics
# -----------------------------------------------------------------------

def test_rate_check_bucket_key_includes_token_hash_and_minute(fake_redis, fixed_time):
    tok = _token(hash_="a" * 64)
    bridge._rate_check(tok, "/send")
    expected_bucket = fixed_time // bridge.RATE_LIMIT_WINDOW_SEC
    expected_key = f"bus:ratelimit:{'a' * 64}:{expected_bucket}"
    assert expected_key in fake_redis.kv
    assert fake_redis.kv[expected_key] == 1


def test_rate_check_sets_ttl_only_on_first_incr(fake_redis, fixed_time):
    tok = _token()
    bridge._rate_check(tok, "/send")
    bucket = fixed_time // bridge.RATE_LIMIT_WINDOW_SEC
    key = f"bus:ratelimit:{'f' * 64}:{bucket}"
    assert fake_redis.ttls[key] == bridge.RATE_LIMIT_TTL_SEC
    # Second call should not overwrite TTL — clear ttls dict and verify
    fake_redis.ttls.clear()
    bridge._rate_check(tok, "/send")
    assert key not in fake_redis.ttls, "EXPIRE should NOT fire on subsequent calls"


def test_rate_check_ttl_exceeds_window(fake_redis, fixed_time):
    """TTL must be > window so a request landing on the boundary doesn't
    lose state if the next request hits the new bucket immediately."""
    assert bridge.RATE_LIMIT_TTL_SEC > bridge.RATE_LIMIT_WINDOW_SEC


# -----------------------------------------------------------------------
# Defense-in-depth: never raise; degrade to skip verdict
# -----------------------------------------------------------------------

def test_rate_check_skips_when_token_hash_missing(fake_redis, fixed_time):
    tok = {"agent": "kasra"}  # no token_hash
    v = bridge._rate_check(tok, "/send")
    assert v["rate_verdict"] == "skip"
    assert v["rate_reason"] == "no_token_hash"


def test_rate_check_swallows_redis_failure(fake_redis, fixed_time):
    fake_redis.raise_on_incr = True
    v = bridge._rate_check(_token(), "/send")
    assert v["rate_verdict"] == "skip"
    assert v["rate_reason"].startswith("err:")


# -----------------------------------------------------------------------
# Audit-stream wiring: verdict lands on sos:audit:bridge:v1
# -----------------------------------------------------------------------

def test_audit_emit_carries_rate_verdict_extra(fake_redis, fixed_time):
    """End-to-end shape: handler-style call writes rate_verdict into the
    same audit record as the endpoint event."""
    tok = _token()
    rate = bridge._rate_check(tok, "/send")
    bridge._audit_emit(tok, "/send", claimed="kasra", target="loom", extra=rate)
    rec = fake_redis.streams["sos:audit:bridge:v1"][0]
    assert rec["endpoint"] == "/send"
    assert rec["rate_verdict"] == "allow"
    assert rec["rate_count"] == "1"
    assert rec["rate_limit"] == str(bridge.RATE_LIMIT_DEFAULT)
    assert rec["rate_endpoint"] == "/send"


# -----------------------------------------------------------------------
# LOCK marker discoverability
# -----------------------------------------------------------------------

def test_l1_marker_present_in_bridge_source():
    from pathlib import Path
    src = Path(bridge.__file__).read_text()
    assert "LOCK-S028-B-1" in src
    assert "L-1" in src
    assert "rate_limit_class" in src
