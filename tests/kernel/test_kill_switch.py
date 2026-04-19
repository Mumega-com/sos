"""Tests for sos.kernel.kill_switch — operator agent kill helper.

Uses fakeredis to exercise the Redis round-trip without requiring a live
broker. Tests skip gracefully when fakeredis isn't installed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

try:
    import fakeredis  # type: ignore[import-untyped]

    HAS_FAKEREDIS = True
except ImportError:  # pragma: no cover
    HAS_FAKEREDIS = False

skipif_no_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS, reason="fakeredis not installed"
)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch):
    """Patch kill_switch._get_client to return a fresh fakeredis instance.

    Returns the shared fake client so tests can also assert on it directly.
    """
    if not HAS_FAKEREDIS:  # pragma: no cover
        pytest.skip("fakeredis not installed")

    from sos.kernel import kill_switch

    fake = fakeredis.FakeStrictRedis(decode_responses=True)

    def _factory(redis_url: str | None = None):  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr(kill_switch, "_get_client", _factory, raising=True)
    return fake


# ---------------------------------------------------------------------------
# 1. Default state — no kill marker exists
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_is_agent_killed_false_by_default(fake_redis) -> None:
    from sos.kernel.kill_switch import is_agent_killed

    assert is_agent_killed("never-killed") is False


# ---------------------------------------------------------------------------
# 2. kill_agent + is_agent_killed round-trip
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_kill_then_is_killed(fake_redis) -> None:
    from sos.kernel.kill_switch import is_agent_killed, kill_agent

    killed_until = kill_agent("naughty-agent")

    assert is_agent_killed("naughty-agent") is True

    # killed_until should be ~24h in the future (allow a generous 5-minute
    # tolerance for slow runners).
    parsed = datetime.fromisoformat(killed_until.replace("Z", "+00:00"))
    delta = parsed - datetime.now(timezone.utc)
    assert timedelta(hours=23, minutes=55) <= delta <= timedelta(hours=24, minutes=5)

    # Redis key exists with the expected value.
    assert fake_redis.get("sos:agent:naughty-agent:killed") == "1"

    # TTL is roughly 24h.
    ttl = fake_redis.ttl("sos:agent:naughty-agent:killed")
    assert 86000 < ttl <= 86400


# ---------------------------------------------------------------------------
# 3. unkill clears the marker
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_unkill_clears(fake_redis) -> None:
    from sos.kernel.kill_switch import is_agent_killed, kill_agent, unkill_agent

    kill_agent("temp-kill")
    assert is_agent_killed("temp-kill") is True

    unkill_agent("temp-kill")
    assert is_agent_killed("temp-kill") is False


# ---------------------------------------------------------------------------
# 4. Fail-soft on read when Redis is unreachable
# ---------------------------------------------------------------------------


def test_is_agent_killed_fails_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any Redis error on the read path must not raise — must return False.

    If the auth path ever blocks on the kill-switch check, a Redis outage
    must not cascade into "everyone is killed". We prefer a false-negative
    (agent slips through once) over a false-positive (agent wrongly blocked).
    """
    from sos.kernel import kill_switch

    class _ExplodingClient:
        def get(self, key: str) -> None:
            raise RuntimeError("connection refused")

    def _factory(redis_url: str | None = None):  # type: ignore[no-untyped-def]
        return _ExplodingClient()

    monkeypatch.setattr(kill_switch, "_get_client", _factory, raising=True)

    # Must not raise.
    assert kill_switch.is_agent_killed("any-agent") is False


# ---------------------------------------------------------------------------
# 5. Writes propagate errors (operator needs to know)
# ---------------------------------------------------------------------------


def test_kill_agent_propagates_write_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """kill_agent writes must surface errors. Unlike is_agent_killed, a
    silent failure on a kill request would leave the operator thinking the
    agent is stopped when it isn't."""
    from sos.kernel import kill_switch

    class _ExplodingClient:
        def set(self, *_a, **_k) -> None:
            raise RuntimeError("connection refused")

    def _factory(redis_url: str | None = None):  # type: ignore[no-untyped-def]
        return _ExplodingClient()

    monkeypatch.setattr(kill_switch, "_get_client", _factory, raising=True)

    with pytest.raises(RuntimeError, match="connection refused"):
        kill_switch.kill_agent("any-agent")


# ---------------------------------------------------------------------------
# 6. Custom TTL takes effect
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_kill_agent_custom_ttl(fake_redis) -> None:
    from sos.kernel.kill_switch import kill_agent

    kill_agent("short-kill", ttl_seconds=60)

    ttl = fake_redis.ttl("sos:agent:short-kill:killed")
    assert 0 < ttl <= 60


@skipif_no_fakeredis
def test_kill_agent_rejects_nonpositive_ttl(fake_redis) -> None:
    from sos.kernel.kill_switch import kill_agent

    with pytest.raises(ValueError):
        kill_agent("bad-ttl", ttl_seconds=0)

    with pytest.raises(ValueError):
        kill_agent("bad-ttl", ttl_seconds=-1)
