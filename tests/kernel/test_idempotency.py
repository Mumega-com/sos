"""Tests for sos.kernel.idempotency — the canonical write-endpoint replay cache.

Uses fakeredis for hermetic testing. All tests skip if fakeredis is not installed.

Invariants under test:
  1. Missing ``Idempotency-Key`` bypasses the cache entirely.
  2. First call (miss) runs ``fn`` and stores the result.
  3. Replay (hit, same body) returns the cached result without running ``fn``.
  4. Hit with a different body raises HTTPException(409).
  5. TTL is applied to stored records.
  6. Keys are tenant-scoped — same raw key across tenants does not collide.
"""
from __future__ import annotations

import pytest

try:
    import fakeredis.aioredis as fake_aioredis  # type: ignore[import-untyped]
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

from fastapi import HTTPException

from sos.kernel.idempotency import with_idempotency

skipif_no_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS, reason="fakeredis not installed"
)


def _fake_redis():
    return fake_aioredis.FakeRedis(decode_responses=True)


class _Counter:
    """Helper — a fn whose call count we can assert on."""

    def __init__(self, return_value):
        self.calls = 0
        self.return_value = return_value

    async def __call__(self):
        self.calls += 1
        return self.return_value


# ---------------------------------------------------------------------------
# 1. key=None — bypass
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
async def test_none_key_bypasses_cache() -> None:
    """With key=None the helper must call fn every time and never touch redis."""
    r = _fake_redis()
    fn = _Counter(return_value={"ok": True})

    result1 = await with_idempotency(
        key=None, tenant="t1", request_body={"a": 1}, fn=fn, redis=r
    )
    result2 = await with_idempotency(
        key=None, tenant="t1", request_body={"a": 1}, fn=fn, redis=r
    )

    assert result1 == {"ok": True}
    assert result2 == {"ok": True}
    assert fn.calls == 2

    # No idempotency key means nothing was written.
    keys = await r.keys("sos:idem:*")
    assert keys == []

    await r.aclose()


# ---------------------------------------------------------------------------
# 2. Miss — fn runs, result stored
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
async def test_first_call_stores_and_returns() -> None:
    r = _fake_redis()
    fn = _Counter(return_value={"created": "tenant_x"})

    result = await with_idempotency(
        key="k-123",
        tenant="acme",
        request_body={"slug": "acme", "email": "a@b.co"},
        fn=fn,
        redis=r,
    )

    assert result == {"created": "tenant_x"}
    assert fn.calls == 1

    raw = await r.get("sos:idem:acme:k-123")
    assert raw is not None
    import json
    record = json.loads(raw)
    assert record["response_body"] == {"created": "tenant_x"}
    assert record["response_status"] == 200
    assert "request_fingerprint" in record
    assert "stored_at" in record

    await r.aclose()


# ---------------------------------------------------------------------------
# 3. Hit (same body) — cached response, fn not re-run
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
async def test_replay_returns_cached_response() -> None:
    r = _fake_redis()
    fn = _Counter(return_value={"id": 42})

    body = {"slug": "acme", "email": "a@b.co"}
    first = await with_idempotency(
        key="dup-key", tenant="acme", request_body=body, fn=fn, redis=r
    )
    second = await with_idempotency(
        key="dup-key", tenant="acme", request_body=body, fn=fn, redis=r
    )

    assert first == {"id": 42}
    assert second == {"id": 42}
    assert fn.calls == 1, "fn must not be called again on replay"

    await r.aclose()


# ---------------------------------------------------------------------------
# 4. Hit, different body — 409
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
async def test_different_body_same_key_raises_409() -> None:
    r = _fake_redis()
    fn = _Counter(return_value={"ok": True})

    await with_idempotency(
        key="reuse-key",
        tenant="acme",
        request_body={"slug": "acme"},
        fn=fn,
        redis=r,
    )

    with pytest.raises(HTTPException) as exc_info:
        await with_idempotency(
            key="reuse-key",
            tenant="acme",
            request_body={"slug": "DIFFERENT"},  # same key, different payload
            fn=fn,
            redis=r,
        )

    assert exc_info.value.status_code == 409
    assert "idempotency" in exc_info.value.detail.lower()
    # fn must not have run a second time.
    assert fn.calls == 1

    await r.aclose()


# ---------------------------------------------------------------------------
# 5. TTL applied
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
async def test_ttl_expiry() -> None:
    """Stored records must carry the configured TTL — verify via redis.ttl()."""
    r = _fake_redis()
    fn = _Counter(return_value={"ok": True})

    await with_idempotency(
        key="ttl-key",
        tenant="acme",
        request_body={"x": 1},
        fn=fn,
        redis=r,
        ttl_s=120,
    )

    ttl = await r.ttl("sos:idem:acme:ttl-key")
    # fakeredis returns an int in seconds; allow small slack for clock jitter.
    assert 0 < ttl <= 120

    # And when we forcibly delete the key (simulating expiry), fn runs again.
    await r.delete("sos:idem:acme:ttl-key")
    await with_idempotency(
        key="ttl-key",
        tenant="acme",
        request_body={"x": 1},
        fn=fn,
        redis=r,
        ttl_s=120,
    )
    assert fn.calls == 2

    await r.aclose()


# ---------------------------------------------------------------------------
# 6. Tenant isolation
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
async def test_tenant_scoped_keys_dont_collide() -> None:
    """Same raw idempotency key under two tenants must be independent records."""
    r = _fake_redis()
    fn_a = _Counter(return_value={"tenant": "a"})
    fn_b = _Counter(return_value={"tenant": "b"})

    result_a = await with_idempotency(
        key="shared-key",
        tenant="tenant-a",
        request_body={"x": 1},
        fn=fn_a,
        redis=r,
    )
    result_b = await with_idempotency(
        key="shared-key",
        tenant="tenant-b",
        request_body={"x": 1},
        fn=fn_b,
        redis=r,
    )

    assert result_a == {"tenant": "a"}
    assert result_b == {"tenant": "b"}
    assert fn_a.calls == 1
    assert fn_b.calls == 1

    # Both records must coexist under distinct namespaced keys.
    assert await r.exists("sos:idem:tenant-a:shared-key") == 1
    assert await r.exists("sos:idem:tenant-b:shared-key") == 1

    # Replay on tenant-a must still return tenant-a's result (no cross-tenant leak).
    replay_a = await with_idempotency(
        key="shared-key",
        tenant="tenant-a",
        request_body={"x": 1},
        fn=fn_a,
        redis=r,
    )
    assert replay_a == {"tenant": "a"}
    assert fn_a.calls == 1  # did not rerun

    await r.aclose()
