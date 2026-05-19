"""
S028 B2 Phase 4 — LOCK-S028-B-1 L-2 /ask concurrency cap tests.

Verifies the per-token concurrency cap on /ask:
  - INCR + EXPIRE 150s on first INCR pattern
  - try/finally DECR on TimeoutExpired, Exception, normal exit
  - feature-flag enforcement: BUS_PHASE4_ASK_ENFORCE off → shadow only,
    on → 429 ask_concurrency_exceeded BEFORE subprocess.run
  - ADV-b: 50 concurrent /ask flood → first 2 succeed (default cap), 48
    receive 429 with flag on
  - ADV-f: defense-in-depth — concurrency layer orthogonal to identity
    binding + rate limit (each layer can pass/fail independently)
  - LOCK marker discoverability
  - Token-level override via top-level `ask_concurrency_cap` field
    (mirror of `rate_limit_class` pattern)

Hermetic discipline: monkeypatches `bridge.r` to a FakeRedis stub +
`subprocess.run` to a recording fake (avoids invoking real openclaw
binary). Time monkeypatched is unnecessary — concurrency cap is
threshold-based on counter, not time-bucket-based.

Phase 4 gate context: ThreadingHTTPServer (line 793) + try/finally on
/ask + INCR/DECR/EXPIRE 150s on `bus:ask:inflight:{token_hash}` are
required prerequisites per Athena P1 WARN 2026-05-05T03:00Z. This file
covers the unit + handler-call layer; service restart is gate-flip-only.
"""
from __future__ import annotations

import io
import json
import os
import subprocess

import pytest

from sos.bus import bridge
from sos.bus.bridge import (
    ASK_CONCURRENCY_DEFAULT,
    ASK_INFLIGHT_TTL_SEC,
    BusHandler,
    _ask_acquire,
    _ask_concurrency_for,
    _ask_enforce_enabled,
    _ask_inflight_key,
    _ask_release,
)


# -----------------------------------------------------------------------
# FakeRedis honoring INCR / DECR / EXPIRE / XADD shapes
# -----------------------------------------------------------------------

class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.streams: dict[str, list[dict]] = {}
        self.raise_on_incr = False
        self.raise_on_decr = False

    def incr(self, key: str) -> int:
        if self.raise_on_incr:
            raise RuntimeError("simulated incr failure")
        self.kv[key] = self.kv.get(key, 0) + 1
        return self.kv[key]

    def decr(self, key: str) -> int:
        if self.raise_on_decr:
            raise RuntimeError("simulated decr failure")
        self.kv[key] = self.kv.get(key, 0) - 1
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


@pytest.fixture(autouse=True)
def _flag_off_default(monkeypatch):
    """Default each test to flag-off (shadow). Tests that need enforce
    explicitly set the env var inside the test body."""
    monkeypatch.delenv("BUS_PHASE4_ASK_ENFORCE", raising=False)


def _token(*, hash_: str = "f" * 64, cap: int | None = None) -> dict:
    t: dict = {"agent": "kasra", "token_hash": hash_, "active": True}
    if cap is not None:
        t["ask_concurrency_cap"] = cap
    return t


# -----------------------------------------------------------------------
# _ask_concurrency_for — capacity resolution
# -----------------------------------------------------------------------

def test_ask_concurrency_default_cap_is_2():
    """Default 2 lines up with brief §3 ADV-b (first 2 of 50 succeed)."""
    assert _ask_concurrency_for(_token()) == ASK_CONCURRENCY_DEFAULT == 2


def test_ask_concurrency_token_override_int():
    assert _ask_concurrency_for(_token(cap=10)) == 10


def test_ask_concurrency_override_zero_falls_back_to_default():
    assert _ask_concurrency_for(_token(cap=0)) == ASK_CONCURRENCY_DEFAULT


def test_ask_concurrency_override_negative_falls_back_to_default():
    assert _ask_concurrency_for(_token(cap=-5)) == ASK_CONCURRENCY_DEFAULT


def test_ask_concurrency_override_garbage_falls_back_to_default():
    """Fail-closed on unparseable values — same posture as
    rate_limit_class string `ELEVATED` falling back to default."""
    for bad in ["abc", None, [], {}]:
        t = _token()
        t["ask_concurrency_cap"] = bad
        assert _ask_concurrency_for(t) == ASK_CONCURRENCY_DEFAULT


# -----------------------------------------------------------------------
# _ask_acquire — INCR + EXPIRE pattern + verdict shape
# -----------------------------------------------------------------------

def test_acquire_first_call_returns_allow_and_increments(fake_redis):
    v = _ask_acquire(_token())
    assert v["ask_concurrency_verdict"] == "allow"
    assert v["ask_count"] == "1"
    assert v["ask_cap"] == str(ASK_CONCURRENCY_DEFAULT)
    assert v["ask_inflight_key"] == f"bus:ask:inflight:{'f' * 64}"
    assert fake_redis.kv[v["ask_inflight_key"]] == 1


def test_acquire_sets_ttl_only_on_first_incr(fake_redis):
    tok = _token()
    _ask_acquire(tok)
    key = f"bus:ask:inflight:{'f' * 64}"
    assert fake_redis.ttls[key] == ASK_INFLIGHT_TTL_SEC
    fake_redis.ttls.clear()
    _ask_acquire(tok)
    assert key not in fake_redis.ttls, "EXPIRE must NOT fire on subsequent INCRs"


def test_acquire_blocks_when_count_exceeds_cap(fake_redis):
    tok = _token()
    _ask_acquire(tok)  # count=1
    _ask_acquire(tok)  # count=2 (at cap)
    v = _ask_acquire(tok)  # count=3 (over cap)
    assert v["ask_concurrency_verdict"] == "would_block_or_block"
    assert v["ask_count"] == "3"


def test_acquire_count_at_cap_still_allowed(fake_redis):
    """Boundary: count == cap is the last allowed; count > cap blocks."""
    tok = _token()
    _ask_acquire(tok)  # 1
    v = _ask_acquire(tok)  # 2 == cap
    assert v["ask_concurrency_verdict"] == "allow"
    assert v["ask_count"] == "2"


def test_acquire_token_override_higher_cap(fake_redis):
    tok = _token(cap=5)
    for i in range(5):
        v = _ask_acquire(tok)
        assert v["ask_concurrency_verdict"] == "allow", f"call {i + 1}"
    v = _ask_acquire(tok)
    assert v["ask_concurrency_verdict"] == "would_block_or_block"


def test_acquire_inflight_key_isolates_per_token(fake_redis):
    """Two tokens with different hashes → independent counters."""
    t1 = _token(hash_="a" * 64)
    t2 = _token(hash_="b" * 64)
    _ask_acquire(t1)
    _ask_acquire(t1)
    _ask_acquire(t1)  # t1 over cap
    v2 = _ask_acquire(t2)  # t2 first call, must allow
    assert v2["ask_concurrency_verdict"] == "allow"


# -----------------------------------------------------------------------
# Defense-in-depth: never raise, never block
# -----------------------------------------------------------------------

def test_acquire_skips_when_token_hash_missing(fake_redis):
    tok = {"agent": "kasra"}  # no token_hash
    v = _ask_acquire(tok)
    assert v["ask_concurrency_verdict"] == "skip"
    assert v["ask_reason"] == "no_token_hash"


def test_acquire_swallows_redis_failure(fake_redis):
    fake_redis.raise_on_incr = True
    v = _ask_acquire(_token())
    assert v["ask_concurrency_verdict"] == "skip"
    assert v["ask_reason"].startswith("err:")


def test_release_swallows_redis_failure(fake_redis):
    fake_redis.raise_on_decr = True
    # Must not raise — release path is finally-block; raising would
    # mask the original handler exception or escape the handler.
    _ask_release(_token())  # no exception


def test_release_no_op_when_token_hash_missing(fake_redis):
    _ask_release({"agent": "kasra"})  # no exception


def test_inflight_key_returns_none_on_missing_hash():
    assert _ask_inflight_key({"agent": "kasra"}) is None
    assert _ask_inflight_key({"agent": "kasra", "token_hash": ""}) is None


# -----------------------------------------------------------------------
# Feature flag — enforce gate
# -----------------------------------------------------------------------

def test_enforce_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BUS_PHASE4_ASK_ENFORCE", raising=False)
    assert _ask_enforce_enabled() is False


def test_enforce_enabled_with_truthy_values(monkeypatch):
    for val in ["1", "true", "yes"]:
        monkeypatch.setenv("BUS_PHASE4_ASK_ENFORCE", val)
        assert _ask_enforce_enabled() is True


def test_enforce_disabled_with_falsy_values(monkeypatch):
    for val in ["0", "false", "no", "", "off"]:
        monkeypatch.setenv("BUS_PHASE4_ASK_ENFORCE", val)
        assert _ask_enforce_enabled() is False


# -----------------------------------------------------------------------
# Handler-level integration: /ask via mock harness
# -----------------------------------------------------------------------

def _make_ask_handler(*, body: dict, token: dict, raw_token: str = "sk-kasra-test"):
    """Mock harness mirroring tests/bus/test_bridge_identity_binding.py
    pattern. Constructs BusHandler bypassing socket setup and force-
    resolves token via monkeypatch on the caller side."""
    h = BusHandler.__new__(BusHandler)
    body_bytes = json.dumps(body).encode()
    headers = {
        "Authorization": f"Bearer {raw_token}",
        "Content-Length": str(len(body_bytes)),
        "Content-Type": "application/json",
    }

    class _Hdr:
        def __init__(self, d): self._d = d
        def get(self, k, default=""): return self._d.get(k, default)

    h.headers = _Hdr(headers)
    h.path = "/ask"
    h.rfile = io.BytesIO(body_bytes)
    h.wfile = io.BytesIO()
    h.command = "POST"
    h.request_version = "HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h._captured_status: list[int] = []  # type: ignore[attr-defined]

    h.send_response = lambda code, msg=None: h._captured_status.append(code)  # type: ignore[assignment]
    h.send_header = lambda *a, **kw: None  # type: ignore[assignment]
    h.end_headers = lambda: None  # type: ignore[assignment]
    return h


def _last_response(handler) -> tuple[int, dict]:
    code = handler._captured_status[-1] if handler._captured_status else 0
    body = json.loads(handler.wfile.getvalue()) if handler.wfile.getvalue() else {}
    return code, body


def _patch_token_resolution(monkeypatch, token: dict, raw_token: str = "sk-kasra-test"):
    monkeypatch.setattr(bridge, "_resolve_token",
                        lambda raw: token if raw == raw_token else None)


class _FakeCompletedProcess:
    """Mimics subprocess.CompletedProcess for happy-path."""
    def __init__(self, returncode: int = 0,
                 stdout: str = '{"result":{"payloads":[{"text":"ok"}]}}',
                 stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_subprocess(monkeypatch, *, raises: BaseException | None = None,
                      returncode: int = 0,
                      stdout: str = '{"result":{"payloads":[{"text":"ok"}]}}'):
    def _fake_run(*args, **kwargs):
        if raises is not None:
            raise raises
        return _FakeCompletedProcess(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(subprocess, "run", _fake_run)


# -----------------------------------------------------------------------
# Try/finally invariants — DECR fires on every exit path
# -----------------------------------------------------------------------

def test_handler_decrs_on_normal_exit(monkeypatch, fake_redis):
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    _patch_subprocess(monkeypatch, returncode=0)
    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    code, _ = _last_response(h)
    assert code == 200
    # Counter incremented to 1, then decremented back to 0
    key = f"bus:ask:inflight:{'f' * 64}"
    assert fake_redis.kv[key] == 0


def test_handler_decrs_on_subprocess_timeout(monkeypatch, fake_redis):
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    _patch_subprocess(monkeypatch,
                      raises=subprocess.TimeoutExpired(cmd="openclaw", timeout=120))
    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    code, body = _last_response(h)
    assert code == 504
    key = f"bus:ask:inflight:{'f' * 64}"
    assert fake_redis.kv[key] == 0, "DECR must run via finally on TimeoutExpired"


def test_handler_decrs_on_subprocess_exception(monkeypatch, fake_redis):
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    _patch_subprocess(monkeypatch, raises=OSError("simulated"))
    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    code, _ = _last_response(h)
    assert code == 500
    key = f"bus:ask:inflight:{'f' * 64}"
    assert fake_redis.kv[key] == 0, "DECR must run via finally on Exception"


def test_handler_decrs_on_returncode_failure(monkeypatch, fake_redis):
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    _patch_subprocess(monkeypatch, returncode=2, stdout="garbage")
    # subprocess.run returns non-zero — handler emits 500 + early return
    # inside try-block; finally still runs.
    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    code, _ = _last_response(h)
    assert code == 500
    key = f"bus:ask:inflight:{'f' * 64}"
    assert fake_redis.kv[key] == 0


def test_handler_decrs_on_validation_failure_400(monkeypatch, fake_redis):
    """Empty agent / message → 400 BEFORE subprocess. Counter still
    INCRed (acquire happens before validation per LOCK comment), so DECR
    must fire on the validation-bail path too."""
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    h = _make_ask_handler(body={"agent": "", "message": ""}, token=tok)
    h.do_POST()
    code, body = _last_response(h)
    assert code == 400
    key = f"bus:ask:inflight:{'f' * 64}"
    assert fake_redis.kv[key] == 0, "DECR must fire on 400 validation bail"


# -----------------------------------------------------------------------
# Feature flag interaction — shadow vs enforce
# -----------------------------------------------------------------------

def test_handler_shadow_mode_does_not_429_over_cap(monkeypatch, fake_redis):
    """Phase 4 pre-flip: counter records over-cap but handler still runs
    subprocess. Audit stream captures the would_block_or_block verdict
    for ops to observe pre-flip distribution."""
    monkeypatch.delenv("BUS_PHASE4_ASK_ENFORCE", raising=False)
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    _patch_subprocess(monkeypatch, returncode=0)

    # Pre-load counter to 5 (well over default cap of 2)
    key = f"bus:ask:inflight:{'f' * 64}"
    fake_redis.kv[key] = 5

    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    code, _ = _last_response(h)
    assert code == 200, "Shadow mode must not 429 even when over cap"
    # Audit emit captures the over-cap verdict
    audit = fake_redis.streams.get("sos:audit:bridge:v1", [])
    assert len(audit) == 1
    assert audit[0]["ask_concurrency_verdict"] == "would_block_or_block"


def test_handler_enforce_mode_returns_429_over_cap(monkeypatch, fake_redis):
    """Phase 4 post-flip: count > cap → 429 ask_concurrency_exceeded
    BEFORE subprocess.run (verifies acquire-then-check-then-release
    sequence; rejection releases the counter so it doesn't drift up
    permanently)."""
    monkeypatch.setenv("BUS_PHASE4_ASK_ENFORCE", "1")
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    # subprocess.run MUST NOT be called when 429 fires; patch with raise
    # to assert the path skips subprocess entirely.
    _patch_subprocess(monkeypatch, raises=AssertionError("subprocess must not run"))

    # Pre-load counter to 5 (over default cap of 2)
    key = f"bus:ask:inflight:{'f' * 64}"
    fake_redis.kv[key] = 5

    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    code, body = _last_response(h)
    assert code == 429
    assert body["error"] == "ask_concurrency_exceeded"
    assert body["retry_after_seconds"] == ASK_INFLIGHT_TTL_SEC
    assert "in flight" in body["message"]
    # Counter released on rejection — not held until handler exit
    assert fake_redis.kv[key] == 5, (
        "On enforce-rejection: INCR happens then DECR releases; "
        "net change = 0 (5 → 6 → 5)"
    )


# -----------------------------------------------------------------------
# ADV-b — single-token concurrency flood (50 → first 2 succeed, 48 reject)
# -----------------------------------------------------------------------

def test_adv_b_flood_first_two_succeed_rest_429(monkeypatch, fake_redis):
    """ADV-b from brief §3: 50 sequential /ask calls (counter never
    decrements between them since handler never returns control to
    DECR) → first 2 succeed (count 1, 2 ≤ cap 2), 48 receive 429 once
    enforce flag flips on. We simulate by NOT decrementing (counter
    pinned) — equivalent to 50 truly concurrent in-flight requests
    under ThreadingHTTPServer."""
    monkeypatch.setenv("BUS_PHASE4_ASK_ENFORCE", "1")
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)

    successes = 0
    rejections = 0

    def _run_one(i):
        nonlocal successes, rejections
        # subprocess only matters for successes — block any unexpected calls
        if i < ASK_CONCURRENCY_DEFAULT:
            _patch_subprocess(monkeypatch, returncode=0)
        else:
            _patch_subprocess(monkeypatch,
                              raises=AssertionError("must not run when 429"))

        # Counter must NOT auto-DECR during flood — we want to simulate
        # 50 in-flight. Override decr to no-op for this test.
        monkeypatch.setattr(fake_redis, "decr", lambda k: fake_redis.kv.get(k, 0))

        h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
        h.do_POST()
        code, _ = _last_response(h)
        if code == 200:
            successes += 1
        elif code == 429:
            rejections += 1

    for i in range(50):
        _run_one(i)

    assert successes == ASK_CONCURRENCY_DEFAULT == 2, (
        f"first {ASK_CONCURRENCY_DEFAULT} requests must succeed, got {successes}"
    )
    assert rejections == 48, f"remaining {50 - ASK_CONCURRENCY_DEFAULT} must 429, got {rejections}"


# -----------------------------------------------------------------------
# ADV-f — defense-in-depth: concurrency layer orthogonal to others
# -----------------------------------------------------------------------

def test_adv_f_concurrency_layer_independent_of_rate_limit(monkeypatch, fake_redis):
    """ADV-f: a token under concurrency cap AND under rate limit AND
    correctly identity-bound succeeds. Each layer gates orthogonally —
    if any layer fails the request fails; if all pass it succeeds. /ask
    has no identity claim (per Phase 3 scope), so binding gate doesn't
    apply; rate limit + concurrency cap both must allow."""
    monkeypatch.setenv("BUS_PHASE4_ASK_ENFORCE", "1")
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    _patch_subprocess(monkeypatch, returncode=0)

    # First call: rate count = 1 (under 60), concurrency count = 1
    # (under 2). Both layers ALLOW → 200.
    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    code, _ = _last_response(h)
    assert code == 200

    # Verify both verdicts landed in audit as ALLOW
    audit = fake_redis.streams.get("sos:audit:bridge:v1", [])
    assert len(audit) == 1
    assert audit[0]["rate_verdict"] == "allow"
    assert audit[0]["ask_concurrency_verdict"] == "allow"


# -----------------------------------------------------------------------
# Audit shape — concurrency verdict in same record as rate verdict
# -----------------------------------------------------------------------

def test_audit_record_carries_both_rate_and_ask_verdicts(monkeypatch, fake_redis):
    tok = _token()
    _patch_token_resolution(monkeypatch, tok)
    _patch_subprocess(monkeypatch, returncode=0)
    h = _make_ask_handler(body={"agent": "loom", "message": "hi"}, token=tok)
    h.do_POST()
    rec = fake_redis.streams["sos:audit:bridge:v1"][0]
    assert rec["endpoint"] == "/ask"
    # Rate verdict fields
    assert rec["rate_verdict"] == "allow"
    assert rec["rate_endpoint"] == "/ask"
    # Concurrency verdict fields
    assert rec["ask_concurrency_verdict"] == "allow"
    assert rec["ask_count"] == "1"
    assert rec["ask_cap"] == str(ASK_CONCURRENCY_DEFAULT)
    assert rec["ask_inflight_key"] == f"bus:ask:inflight:{'f' * 64}"


# -----------------------------------------------------------------------
# LOCK marker discoverability (lint:locks substitute)
# -----------------------------------------------------------------------

def test_l2_marker_present_in_bridge_source():
    from pathlib import Path
    src = Path(bridge.__file__).read_text()
    assert "LOCK-S028-B-1" in src
    assert "L-2" in src
    assert "ask_concurrency_cap" in src
    assert "ASK_CONCURRENCY_DEFAULT" in src
    assert "ASK_INFLIGHT_TTL_SEC" in src


def test_threading_http_server_used_in_main():
    """Substrate shape verification: server bind line uses
    ThreadingHTTPServer per Athena P1 WARN 2026-05-05T03:00Z."""
    from pathlib import Path
    src = Path(bridge.__file__).read_text()
    assert "ThreadingHTTPServer((\"0.0.0.0\", PORT), BusHandler)" in src
    # The non-threading HTTPServer must NOT be used to construct the
    # bus server. Substring-match excluding the Threading- prefix.
    bind_marker = "HTTPServer((\"0.0.0.0\", PORT), BusHandler)"
    # Find every occurrence; each must be preceded by "Threading"
    idx = 0
    while True:
        idx = src.find(bind_marker, idx)
        if idx == -1:
            break
        # 9 chars before is "Threading"
        assert src[max(0, idx - 9):idx] == "Threading", (
            f"Non-Threading HTTPServer bind found at offset {idx}"
        )
        idx += len(bind_marker)


def test_inflight_ttl_exceeds_subprocess_timeout():
    """TTL 150s > subprocess timeout 120s + post-work margin. Required
    for crash-recovery defense-in-depth: if bridge SIGKILLed mid-/ask,
    counter recovers via TTL rather than leaking permanently."""
    assert ASK_INFLIGHT_TTL_SEC > 120
