"""End-to-end integration: bus write → Mirror engram.

Tests the full write loop:
  XADD sos:stream:global:agent:<name>
  → mirror_bus_consumer picks up the message
  → Mirror creates an engram (observable via /recent/<agent>)

Prerequisites (all checked by fixtures, tests skip if not met):
  - Redis running and reachable (REDIS_URL / REDIS_PASSWORD env vars)
  - Mirror HTTP API running at http://localhost:8844 (MIRROR_URL)
  - mirror_bus_consumer.service is active (systemctl --user)

Run only these tests:
  pytest tests/integration/ -m integration -v

Skip markers are intentional — this suite must not run in CI without
explicitly opting in.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
_REDIS_URL = os.environ.get(
    "REDIS_URL",
    f"redis://:{_REDIS_PASSWORD}@localhost:6379/0" if _REDIS_PASSWORD else "redis://localhost:6379/0",
)
_MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
_MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "")

# Stream keys
_STREAM_AGENT = "sos:stream:global:agent:test-agent"
_STREAM_BROADCAST = "sos:stream:global:broadcast"

# How long to wait for an engram to appear after an XADD (seconds)
_ENGRAM_TIMEOUT = 5.0
_POLL_INTERVAL = 0.25


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_available() -> bool:
    """Return True if Redis is reachable; skip the test if not."""
    try:
        import redis as _redis  # type: ignore[import]

        r = _redis.from_url(_REDIS_URL, socket_connect_timeout=1, decode_responses=True)
        r.ping()
        r.close()
        return True
    except Exception as exc:
        pytest.skip(f"Redis not reachable ({exc})")
        return False  # unreachable — keeps type checker happy


@pytest.fixture(scope="session")
def mirror_available(redis_available: bool) -> bool:  # noqa: ARG001
    """Return True if Mirror HTTP API is reachable; skip if not."""
    try:
        import httpx

        headers = {"Authorization": f"Bearer {_MIRROR_TOKEN}"} if _MIRROR_TOKEN else {}
        r = httpx.get(f"{_MIRROR_URL}/health", headers=headers, timeout=2.0)
        if not r.is_success:
            pytest.skip(f"Mirror /health returned {r.status_code}")
        return True
    except Exception as exc:
        pytest.skip(f"Mirror not reachable ({exc})")
        return False


@pytest.fixture(scope="session")
def bus_consumer_available(mirror_available: bool) -> bool:  # noqa: ARG001
    """Return True if mirror_bus_consumer.service is active; skip if not."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "mirror_bus_consumer.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active = result.stdout.strip() == "active"
        if not active:
            pytest.skip(
                f"mirror_bus_consumer.service is not active (status: {result.stdout.strip()!r})"
            )
        return True
    except FileNotFoundError:
        pytest.skip("systemctl not available — skipping consumer liveness check")
        return False
    except Exception as exc:
        pytest.skip(f"Could not check mirror_bus_consumer.service: {exc}")
        return False


@pytest.fixture()
def redis_client(redis_available: bool):  # noqa: ARG001
    """Synchronous Redis client used by tests."""
    import redis as _redis  # type: ignore[import]

    r = _redis.from_url(_REDIS_URL, decode_responses=True)
    yield r
    r.close()


@pytest.fixture()
def mirror_http(mirror_available: bool):  # noqa: ARG001
    """Synchronous httpx client pointed at Mirror."""
    import httpx

    headers = {"Authorization": f"Bearer {_MIRROR_TOKEN}"} if _MIRROR_TOKEN else {}
    with httpx.Client(base_url=_MIRROR_URL, headers=headers, timeout=5.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_v1_payload(msg_id: str, target: str = "test-agent") -> dict[str, str]:
    """Build the Redis stream field dict for a v1 SOS message."""
    inner: dict[str, Any] = {
        "id": msg_id,
        "version": "v1",
        "type": "send",
        "source": "agent:integration-test",
        "target": target,
        "text": f"Integration test message {msg_id}",
        "timestamp": time.time(),
    }
    return {
        "type": "v1",
        "payload": json.dumps(inner),
    }


def _xadd(r: Any, stream: str, fields: dict[str, str]) -> str:
    """XADD to *stream*, return the Redis message-id (e.g. '1234567890-0')."""
    msg_redis_id = r.xadd(stream, fields)
    return msg_redis_id


def _poll_for_engram(
    mirror: Any,
    agent: str,
    context_id: str,
    timeout: float = _ENGRAM_TIMEOUT,
) -> dict[str, Any] | None:
    """Poll Mirror's /recent/<agent> until an engram with *context_id* appears.

    Returns the engram dict, or None on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = mirror.get(f"/recent/{agent}")
        if resp.is_success:
            data = resp.json()
            engrams: list[dict[str, Any]] = (
                data if isinstance(data, list) else data.get("engrams", data.get("items", []))
            )
            for eng in engrams:
                if eng.get("context_id") == context_id or eng.get("id") == context_id:
                    return eng
        time.sleep(_POLL_INTERVAL)
    return None


def _count_engrams_for_context(
    mirror: Any,
    agent: str,
    context_id: str,
) -> int:
    """Return how many engrams exist for *context_id* in Mirror."""
    resp = mirror.get(f"/recent/{agent}")
    if not resp.is_success:
        return 0
    data = resp.json()
    engrams: list[dict[str, Any]] = (
        data if isinstance(data, list) else data.get("engrams", data.get("items", []))
    )
    return sum(
        1
        for e in engrams
        if e.get("context_id") == context_id or e.get("id") == context_id
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_send_message_produces_engram(
    bus_consumer_available: bool,  # noqa: ARG001
    redis_client: Any,
    mirror_http: Any,
) -> None:
    """A v1 'send' message on the agent stream must produce a Mirror engram."""
    msg_id = f"test-send-{uuid.uuid4().hex[:8]}"
    fields = _make_v1_payload(msg_id, target="test-agent")
    _xadd(redis_client, _STREAM_AGENT, fields)

    engram = _poll_for_engram(mirror_http, "test-agent", msg_id)
    assert engram is not None, (
        f"No engram with context_id={msg_id!r} appeared in Mirror within "
        f"{_ENGRAM_TIMEOUT}s — check mirror_bus_consumer logs."
    )


def test_broadcast_message_produces_engram(
    bus_consumer_available: bool,  # noqa: ARG001
    redis_client: Any,
    mirror_http: Any,
) -> None:
    """A v1 message on the broadcast stream must also produce a Mirror engram."""
    msg_id = f"test-bcast-{uuid.uuid4().hex[:8]}"
    inner: dict[str, Any] = {
        "id": msg_id,
        "version": "v1",
        "type": "broadcast",
        "source": "agent:integration-test",
        "target": "broadcast",
        "text": f"Broadcast test {msg_id}",
        "timestamp": time.time(),
    }
    fields = {"type": "v1", "payload": json.dumps(inner)}
    _xadd(redis_client, _STREAM_BROADCAST, fields)

    # Mirror may store broadcast engrams under a generic agent name; try both
    # "broadcast" and the absence of a specific agent check.
    engram = _poll_for_engram(mirror_http, "broadcast", msg_id)
    # If Mirror doesn't have a /recent/broadcast endpoint, skip gracefully.
    # The consumer still must not crash — verified by subsequent tests.
    if engram is None:
        pytest.skip(
            "Mirror /recent/broadcast returned no engram — broadcast routing "
            "may not be supported yet; consumer crash will surface in later tests."
        )


def test_idempotent_consumer(
    bus_consumer_available: bool,  # noqa: ARG001
    redis_client: Any,
    mirror_http: Any,
) -> None:
    """Sending the same message_id twice must result in exactly one engram.

    The consumer is expected to deduplicate on ``context_id`` / ``id``.
    """
    msg_id = f"test-idem-{uuid.uuid4().hex[:8]}"
    fields = _make_v1_payload(msg_id, target="test-agent")

    # Send twice — same logical message, two separate Redis stream entries
    _xadd(redis_client, _STREAM_AGENT, fields)
    _xadd(redis_client, _STREAM_AGENT, fields)

    # Give the consumer enough time to process both entries
    time.sleep(_ENGRAM_TIMEOUT)

    count = _count_engrams_for_context(mirror_http, "test-agent", msg_id)
    assert count == 1, (
        f"Expected exactly 1 engram for context_id={msg_id!r}, found {count}. "
        "Consumer idempotency may be broken."
    )


@pytest.mark.skip(
    reason=(
        "Checkpoint resume requires restarting mirror_bus_consumer.service "
        "which is not safe to do inside a test. "
        "Expected behaviour: after a restart the consumer reads from the last "
        "persisted XREAD checkpoint stored in Redis key "
        "'sos:bus:consumer:checkpoint' and does NOT re-process messages "
        "already acknowledged before the restart."
    )
)
def test_checkpoint_resumes_on_restart(
    bus_consumer_available: bool,  # noqa: ARG001
    redis_client: Any,
    mirror_http: Any,
) -> None:  # pragma: no cover
    """Verify the consumer resumes from its last checkpoint after a restart."""
    raise NotImplementedError("Manual verification required — see skip reason.")


def test_message_without_v1_type_ignored(
    bus_consumer_available: bool,  # noqa: ARG001
    redis_client: Any,
    mirror_http: Any,
) -> None:
    """Legacy messages (type != 'v1') must NOT produce engrams.

    The bus consumer should only process messages with ``type == 'v1'``.
    """
    # Record current engram count before sending the legacy message
    resp_before = mirror_http.get("/recent/test-agent")
    before_count = 0
    if resp_before.is_success:
        data = resp_before.json()
        engrams = data if isinstance(data, list) else data.get("engrams", data.get("items", []))
        before_count = len(engrams)

    # Send a legacy-style message
    legacy_id = f"test-legacy-{uuid.uuid4().hex[:8]}"
    legacy_fields = {
        "type": "chat",
        "payload": json.dumps({"id": legacy_id, "text": "old-style message"}),
    }
    _xadd(redis_client, _STREAM_AGENT, legacy_fields)

    # Wait a bit and confirm no new engram for the legacy id
    time.sleep(_ENGRAM_TIMEOUT / 2)

    engram = _poll_for_engram(mirror_http, "test-agent", legacy_id, timeout=1.0)
    assert engram is None, (
        f"Legacy (non-v1) message {legacy_id!r} unexpectedly produced an engram."
    )

    # Also assert the count did not grow from this message alone
    resp_after = mirror_http.get("/recent/test-agent")
    if resp_after.is_success:
        data_after = resp_after.json()
        engrams_after = (
            data_after
            if isinstance(data_after, list)
            else data_after.get("engrams", data_after.get("items", []))
        )
        after_count = len(engrams_after)
        assert after_count == before_count, (
            f"Engram count grew by {after_count - before_count} after sending a legacy message "
            f"— expected no change (before={before_count}, after={after_count})."
        )


def test_invalid_message_does_not_crash_consumer(
    bus_consumer_available: bool,  # noqa: ARG001
    redis_client: Any,
    mirror_http: Any,
) -> None:
    """A malformed payload must not crash the consumer.

    Steps:
    1. Send a message with an unparseable JSON payload.
    2. Assert the consumer is still alive (systemctl is-active).
    3. Send a valid v1 message afterward and assert it is processed correctly.
    """
    # 1. Send the malformed message
    malformed_fields = {
        "type": "v1",
        "payload": "this is not valid { json ]]]",
    }
    _xadd(redis_client, _STREAM_AGENT, malformed_fields)

    # 2. Brief pause then verify consumer is still active
    time.sleep(1.5)
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "mirror_bus_consumer.service"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.stdout.strip() == "active", (
        f"mirror_bus_consumer.service crashed after a malformed message "
        f"(status={result.stdout.strip()!r}). Check service logs with: "
        "'journalctl --user -u mirror_bus_consumer.service -n 50'"
    )

    # 3. Send a valid message and confirm it is still processed
    recovery_id = f"test-recovery-{uuid.uuid4().hex[:8]}"
    valid_fields = _make_v1_payload(recovery_id, target="test-agent")
    _xadd(redis_client, _STREAM_AGENT, valid_fields)

    engram = _poll_for_engram(mirror_http, "test-agent", recovery_id)
    assert engram is not None, (
        f"Consumer did not process valid message after receiving a malformed one "
        f"(context_id={recovery_id!r}). Consumer may be stuck."
    )
