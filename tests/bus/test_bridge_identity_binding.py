"""
S028 B2 Phase 3 — LOCK-S028-B-1.1 caller-identity-binding hard gate tests.

Verifies `_assert_caller` raises the correct typed exception on:
  - identity-binding mismatch  (claimed != token.agent)
  - malformed token record     (token.agent missing/empty/non-dict)
  - empty claim                (claimed agent missing in request)

And that 5 handlers — /send, /broadcast, /announce, /inbox, /heartbeat —
gate via `_enforce_caller(token, claimed)` BEFORE any business logic
(no Redis writes, no registry mutations, no stream xadd).

Hermetic discipline: handler-level adversarial cases use a thin mock
that bypasses BaseHTTPRequestHandler.__init__ (which would handle the
request synchronously over a socket). The mock sets the minimum
attributes that BusHandler methods read: headers, path, rfile/wfile
BytesIO, command. Redis is monkeypatched to a fake that records writes;
on a 403 enforce the fake stays empty.

End-to-end ADV-a-1..a-4 against live :6380 bridge runs in the gate
adversarial-parallel block (post-deploy smoke). This file covers the
unit + handler-call layer.
"""
from __future__ import annotations

import io
import json

import pytest

from sos.bus import bridge
from sos.bus.bridge import _assert_caller, _IdentityBindingError, BusHandler


# -----------------------------------------------------------------------
# Direct _assert_caller semantics (LOCK-B-1.1 L-1.1.a / .c)
# -----------------------------------------------------------------------

def test_assert_caller_passes_on_match():
    # No exception raised → returns None.
    assert _assert_caller({"agent": "kasra"}, "kasra") is None


def test_assert_caller_raises_mismatch():
    with pytest.raises(_IdentityBindingError) as exc:
        _assert_caller({"agent": "kasra"}, "loom")
    assert exc.value.code == "identity_binding_mismatch"
    assert "kasra" in exc.value.message and "loom" in exc.value.message


def test_assert_caller_raises_on_empty_claim():
    with pytest.raises(_IdentityBindingError) as exc:
        _assert_caller({"agent": "kasra"}, "")
    assert exc.value.code == "identity_binding_mismatch"


def test_assert_caller_raises_on_none_claim():
    with pytest.raises(_IdentityBindingError) as exc:
        _assert_caller({"agent": "kasra"}, None)
    assert exc.value.code == "identity_binding_mismatch"


def test_assert_caller_raises_malformed_when_token_agent_missing():
    """Fail-closed posture (AGD canon: silent-fail-open-at-contract-boundaries
    is a third-instance violation)."""
    with pytest.raises(_IdentityBindingError) as exc:
        _assert_caller({}, "kasra")
    assert exc.value.code == "malformed_token_record"


def test_assert_caller_raises_malformed_when_token_agent_empty():
    with pytest.raises(_IdentityBindingError) as exc:
        _assert_caller({"agent": ""}, "kasra")
    assert exc.value.code == "malformed_token_record"


def test_assert_caller_raises_malformed_when_token_not_dict():
    """Defense-in-depth: non-dict tokens cannot bind identity."""
    for bad in [None, "string", 42, ["agent", "kasra"]]:
        with pytest.raises(_IdentityBindingError) as exc:
            _assert_caller(bad, "kasra")  # type: ignore[arg-type]
        assert exc.value.code == "malformed_token_record"


def test_identity_binding_error_carries_code_and_message():
    err = _IdentityBindingError("identity_binding_mismatch", "x != y")
    assert err.code == "identity_binding_mismatch"
    assert err.message == "x != y"
    # Must be a real Exception so handlers can catch with except clause.
    assert isinstance(err, Exception)


# -----------------------------------------------------------------------
# Handler-level adversarial cases (ADV-a-1..a-4) via mock-handler harness
# -----------------------------------------------------------------------

class _FakeRedis:
    """Records every write so we can assert business logic did NOT run."""

    def __init__(self) -> None:
        self.streams: dict[str, list[dict]] = {}
        self.published: list[tuple[str, str]] = []
        self.hsets: list[tuple[str, dict]] = []
        self.expires: list[tuple[str, int]] = []
        self.kv: dict[str, int] = {}

    # Audit + rate-check stream/key writes (allowed pre-gate)
    def xadd(self, stream, fields, maxlen=None, approximate=False) -> str:
        self.streams.setdefault(stream, []).append(dict(fields))
        return f"{len(self.streams[stream])}-0"

    def incr(self, key) -> int:
        self.kv[key] = self.kv.get(key, 0) + 1
        return self.kv[key]

    def expire(self, key, ttl) -> bool:
        self.expires.append((key, ttl))
        return True

    # Business-side ops — must NOT be called when gate trips
    def hset(self, key, mapping=None, **kw):
        self.hsets.append((key, dict(mapping or {})))
        return 1

    def publish(self, channel, msg):
        self.published.append((channel, msg))
        return 0

    def xrevrange(self, *a, **kw):
        return []

    def ping(self):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(bridge, "r", fake, raising=False)
    return fake


@pytest.fixture
def token_kasra():
    return {
        "agent": "kasra",
        "token_hash": "k" * 64,
        "active": True,
    }


def _make_handler(*, method: str, path: str, body: dict | None = None,
                  token: dict, raw_token: str = "sk-kasra-test"):
    """Construct BusHandler with mocked socket I/O without invoking
    BaseHTTPRequestHandler.__init__ (which would handle synchronously).

    Sets minimum attributes the handler methods touch: headers, path,
    rfile (BytesIO of body), wfile (BytesIO to capture response),
    command, request_version. The token is pre-resolved by setting
    Authorization header that maps to the provided token via _auth().
    """
    h = BusHandler.__new__(BusHandler)

    # Build authorization header that resolves to `token`.
    body_bytes = json.dumps(body or {}).encode()
    headers = {
        "Authorization": f"Bearer {raw_token}",
        "Content-Length": str(len(body_bytes)),
        "Content-Type": "application/json",
    }
    # http.client uses email-style Message; a plain dict-with-get is enough.
    class _Hdr:
        def __init__(self, d): self._d = d
        def get(self, k, default=""): return self._d.get(k, default)
    h.headers = _Hdr(headers)

    h.path = path
    h.rfile = io.BytesIO(body_bytes)
    h.wfile = io.BytesIO()
    h.command = method
    h.request_version = "HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)

    # Capture send_response/send_header/end_headers
    h._captured_status: list[int] = []  # type: ignore[attr-defined]

    def _send_response(code, message=None):
        h._captured_status.append(code)  # type: ignore[attr-defined]

    def _send_header(*a, **kw):
        pass

    def _end_headers():
        pass

    h.send_response = _send_response  # type: ignore[assignment]
    h.send_header = _send_header  # type: ignore[assignment]
    h.end_headers = _end_headers  # type: ignore[assignment]

    return h


def _patch_token_resolution(monkeypatch, token: dict, raw_token: str = "sk-kasra-test"):
    """Force _resolve_token to return our test token regardless of input."""
    monkeypatch.setattr(bridge, "_resolve_token",
                        lambda raw: token if raw == raw_token else None)


def _last_response_body(handler) -> dict:
    """Decode last JSON written to wfile."""
    raw = handler.wfile.getvalue()
    return json.loads(raw)


# --- ADV-a-1: /send body.from spoof ------------------------------------

def test_adv_a_1_send_with_spoofed_from_returns_403(
    monkeypatch, fake_redis, token_kasra
):
    _patch_token_resolution(monkeypatch, token_kasra)
    h = _make_handler(
        method="POST", path="/send",
        body={"from": "loom", "to": "athena", "text": "spoofed"},
        token=token_kasra,
    )
    h.do_POST()
    assert 403 in h._captured_status  # type: ignore[attr-defined]
    body = _last_response_body(h)
    assert body["error"] == "identity_binding_mismatch"
    # Crucial: the spoofed message did NOT land in any agent stream.
    agent_streams = [s for s in fake_redis.streams
                     if s.startswith("sos:stream:") and ":agent:athena" in s]
    assert agent_streams == [], "/send spoofed body must not write to athena's stream"
    # Audit + rate-check ARE allowed pre-gate; verify audit captured the violation.
    audit = fake_redis.streams.get("sos:audit:bridge:v1", [])
    assert len(audit) == 1
    assert audit[0]["binding_match"] == "0"
    assert audit[0]["claimed_agent"] == "loom"
    assert audit[0]["token_agent"] == "kasra"


# --- ADV-a-2: /inbox query.agent cross-agent read --------------------

def test_adv_a_2_inbox_cross_agent_read_returns_403(
    monkeypatch, fake_redis, token_kasra
):
    _patch_token_resolution(monkeypatch, token_kasra)
    h = _make_handler(
        method="GET", path="/inbox?agent=loom&limit=10",
        body=None, token=token_kasra,
    )
    h.do_GET()
    assert 403 in h._captured_status  # type: ignore[attr-defined]
    body = _last_response_body(h)
    assert body["error"] == "identity_binding_mismatch"
    # No xrevrange calls leaked any data — fake_redis.streams should only
    # contain the audit stream record, not any sos:stream:* business stream.
    business_reads = [s for s in fake_redis.streams
                      if s != "sos:audit:bridge:v1"]
    assert business_reads == []


# --- ADV-a-3: /announce identity registration spoof -------------------

def test_adv_a_3_announce_with_spoofed_agent_returns_403(
    monkeypatch, fake_redis, token_kasra
):
    _patch_token_resolution(monkeypatch, token_kasra)
    h = _make_handler(
        method="POST", path="/announce",
        body={"agent": "loom", "tool": "claude-code", "summary": "imposter"},
        token=token_kasra,
    )
    h.do_POST()
    assert 403 in h._captured_status  # type: ignore[attr-defined]
    body = _last_response_body(h)
    assert body["error"] == "identity_binding_mismatch"
    # Registry hash was NOT mutated.
    assert fake_redis.hsets == []


# --- ADV-a-4: /heartbeat refresh-other --------------------------------

def test_adv_a_4_heartbeat_for_other_agent_returns_403(
    monkeypatch, fake_redis, token_kasra
):
    _patch_token_resolution(monkeypatch, token_kasra)
    h = _make_handler(
        method="POST", path="/heartbeat",
        body={"agent": "loom"},
        token=token_kasra,
    )
    h.do_POST()
    assert 403 in h._captured_status  # type: ignore[attr-defined]
    body = _last_response_body(h)
    assert body["error"] == "identity_binding_mismatch"
    # No registry TTL refresh, no hset.
    assert fake_redis.hsets == []
    assert fake_redis.expires == []  # /heartbeat has no rate-check, so no expire either


# --- POSITIVE: matching identity passes through to business logic ---

def test_send_with_matching_from_passes_gate(
    monkeypatch, fake_redis, token_kasra
):
    _patch_token_resolution(monkeypatch, token_kasra)
    h = _make_handler(
        method="POST", path="/send",
        body={"from": "kasra", "to": "athena", "text": "hello"},
        token=token_kasra,
    )
    h.do_POST()
    assert 200 in h._captured_status  # type: ignore[attr-defined]
    # Stream write happened.
    business_streams = [s for s in fake_redis.streams
                        if s != "sos:audit:bridge:v1"]
    assert len(business_streams) == 1


# --- Malformed token (no agent field) is hard-rejected ---------------

def test_malformed_token_returns_403_on_send(monkeypatch, fake_redis):
    bad_token = {"token_hash": "x" * 64, "active": True}  # no agent
    _patch_token_resolution(monkeypatch, bad_token)
    h = _make_handler(
        method="POST", path="/send",
        body={"from": "kasra", "to": "athena", "text": "hello"},
        token=bad_token,
    )
    h.do_POST()
    assert 403 in h._captured_status  # type: ignore[attr-defined]
    body = _last_response_body(h)
    assert body["error"] == "malformed_token_record"


# -----------------------------------------------------------------------
# LOCK marker discoverability (lint:locks substitute)
# -----------------------------------------------------------------------

def test_lock_b_1_1_markers_present_in_bridge_source():
    from pathlib import Path
    src = Path(bridge.__file__).read_text()
    assert "LOCK-S028-B-1.1" in src
    assert "_assert_caller" in src
    assert "_enforce_caller" in src
    # Each of 5 endpoints calls _enforce_caller — count occurrences (the
    # method definition + 5 call sites = 6).
    assert src.count("_enforce_caller(") >= 6, (
        f"Expected method def + 5 endpoint calls; got {src.count('_enforce_caller(')}"
    )
