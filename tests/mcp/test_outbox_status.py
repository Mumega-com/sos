"""Unit tests for the F-17 outbox.status aggregator.

S024 Track F Phase 2 LOCK F-17.

Covers the three substrate branches (Mirror, SOS, Inkwell-incoming) and
the response-contract shape from v0.5 brief §6.6 F-17:

  {
    "components": {
      "mirror":           {dlq_count, pending_count, backend, [last_error]},
      "sos":              {dlq_count, pending_count, backend: "best_effort"},
      "inkwell_incoming": {dlq_count, pending_count, backend: "not_configured"},
    },
    "alert_thresholds": {dlq_count, pending_count, stale_pending_seconds},
  }

Mirror's branch is exercised by stubbing `requests.get` so the test does
not require a running Mirror.
"""
from __future__ import annotations

import sys
import types

import pytest

# Mirror import stubs (same pattern as test_tenant_scope.py).
_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

import sos.mcp.sos_mcp_sse as sse


# ---------------------------------------------------------------------------
# Test doubles for requests.get
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def _patch_mirror_token(monkeypatch, value: str = "sk-mirror-admin-test"):
    monkeypatch.setattr(sse, "MIRROR_ADMIN_TOKEN", value)
    monkeypatch.setattr(sse, "MIRROR_ADMIN_HEADERS", {
        "Authorization": f"Bearer {value}",
        "Content-Type": "application/json",
    })


# ---------------------------------------------------------------------------
# _mirror_outbox_status_sync
# ---------------------------------------------------------------------------


def test_mirror_branch_not_configured_when_admin_token_missing(monkeypatch):
    _patch_mirror_token(monkeypatch, "")
    result = sse._mirror_outbox_status_sync()
    assert result["backend"] == "not_configured"
    assert "MIRROR_ADMIN_TOKEN" in result["last_error"]
    assert result["pending_count"] == 0
    assert result["dlq_count"] == 0


def test_mirror_branch_real_when_native_backend(monkeypatch):
    _patch_mirror_token(monkeypatch)

    def fake_get(url, headers=None, timeout=None):
        assert "/admin/outbox/status" in url
        return _Resp(200, {
            "queue": "inkwell-receipts",
            "backend": "native",
            "enabled": True,
            "pending_count": 3,
            "in_flight_count": 1,
            "dlq_count": 2,
        })

    monkeypatch.setattr(sse.requests, "get", fake_get)
    result = sse._mirror_outbox_status_sync()
    assert result["backend"] == "native"
    assert result["pending_count"] == 3
    assert result["dlq_count"] == 2


def test_mirror_branch_memory_backend_passes_through(monkeypatch):
    _patch_mirror_token(monkeypatch)
    monkeypatch.setattr(sse.requests, "get", lambda *a, **k: _Resp(200, {
        "queue": "inkwell-receipts",
        "backend": "memory",
        "enabled": True,
        "pending_count": 0,
        "in_flight_count": 0,
        "dlq_count": 0,
    }))
    result = sse._mirror_outbox_status_sync()
    assert result["backend"] == "memory"


def test_mirror_branch_not_configured_when_flag_off(monkeypatch):
    _patch_mirror_token(monkeypatch)
    monkeypatch.setattr(sse.requests, "get", lambda *a, **k: _Resp(200, {
        "queue": "inkwell-receipts",
        "backend": "disabled",
        "enabled": False,
        "pending_count": 0,
        "in_flight_count": 0,
        "dlq_count": 0,
    }))
    result = sse._mirror_outbox_status_sync()
    assert result["backend"] == "not_configured"
    assert "MIRROR_OUTBOX_ENABLED" in result["last_error"]


def test_mirror_branch_error_when_endpoint_throws(monkeypatch):
    _patch_mirror_token(monkeypatch)

    def boom(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(sse.requests, "get", boom)
    result = sse._mirror_outbox_status_sync()
    assert result["backend"] == "error"
    assert "connection refused" in result["last_error"]
    # Counts default to zero so dashboards don't false-page.
    assert result["pending_count"] == 0
    assert result["dlq_count"] == 0


def test_mirror_branch_error_when_5xx(monkeypatch):
    _patch_mirror_token(monkeypatch)
    monkeypatch.setattr(sse.requests, "get", lambda *a, **k: _Resp(500, {}))
    result = sse._mirror_outbox_status_sync()
    assert result["backend"] == "error"


# ---------------------------------------------------------------------------
# SOS branch — S025 A-1 promoted from best_effort placeholder to native
# durable counts via Redis Streams (XPENDING + dlq:* XLEN).
# ---------------------------------------------------------------------------


def test_sos_branch_native_when_redis_returns_zero(monkeypatch):
    """Empty bus substrate ⇒ backend=native, pending=0, dlq=0.

    "native" is the durability claim, not "non-zero numbers". A clean
    substrate must report `native` so dashboards distinguish it from
    `not_configured` (no outbox at all) or `error` (couldn't read).
    """
    monkeypatch.setattr(
        "sos.services.bus.outbox_stats.collect_bus_outbox_stats_sync",
        lambda client: {"pending_count": 0, "dlq_count": 0},
    )
    result = sse._sos_outbox_status_sync()
    assert result["backend"] == "native"
    assert result["pending_count"] == 0
    assert result["dlq_count"] == 0


def test_sos_branch_native_passes_through_real_counts(monkeypatch):
    monkeypatch.setattr(
        "sos.services.bus.outbox_stats.collect_bus_outbox_stats_sync",
        lambda client: {"pending_count": 7, "dlq_count": 3},
    )
    result = sse._sos_outbox_status_sync()
    assert result["backend"] == "native"
    assert result["pending_count"] == 7
    assert result["dlq_count"] == 3


def test_sos_branch_error_when_redis_raises(monkeypatch):
    """Redis outage ⇒ backend=error; counts default to 0 so the
    aggregator doesn't false-page on a transient hiccup."""

    def boom(client):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(
        "sos.services.bus.outbox_stats.collect_bus_outbox_stats_sync",
        boom,
    )
    result = sse._sos_outbox_status_sync()
    assert result["backend"] == "error"
    assert "redis unreachable" in result["last_error"]
    assert result["pending_count"] == 0
    assert result["dlq_count"] == 0


def test_inkwell_incoming_branch_not_configured():
    result = sse._inkwell_outbox_status_sync()
    assert result["backend"] == "not_configured"


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def test_aggregate_returns_brief_compliant_shape(monkeypatch):
    _patch_mirror_token(monkeypatch)
    monkeypatch.setattr(sse.requests, "get", lambda *a, **k: _Resp(200, {
        "queue": "inkwell-receipts",
        "backend": "native",
        "enabled": True,
        "pending_count": 0,
        "in_flight_count": 0,
        "dlq_count": 0,
    }))
    monkeypatch.setattr(
        "sos.services.bus.outbox_stats.collect_bus_outbox_stats_sync",
        lambda client: {"pending_count": 0, "dlq_count": 0},
    )
    agg = sse._aggregate_outbox_status_sync()
    assert "components" in agg
    assert "alert_thresholds" in agg
    assert set(agg["components"].keys()) == {"mirror", "sos", "inkwell_incoming"}
    assert agg["components"]["mirror"]["backend"] == "native"
    # S025 A-1 — SOS branch promoted from best_effort to native.
    assert agg["components"]["sos"]["backend"] == "native"
    assert agg["components"]["inkwell_incoming"]["backend"] == "not_configured"
    # Alert thresholds match v0.5 brief §6.6 F-17 contract.
    assert agg["alert_thresholds"] == {
        "dlq_count": 10,
        "pending_count": 1000,
        "stale_pending_seconds": 3600,
    }


def test_outbox_status_tool_definition_present():
    """The tool is exposed by get_tools()."""
    names = {t["name"] for t in sse.get_tools()}
    assert "outbox_status" in names


def test_outbox_status_listed_in_strict_system_only_tools():
    """BLOCK-P1-5 closure: tenant tokens must be denied at dispatch.

    The outbox_status tool surfaces operator-only data (cross-substrate
    DLQ depths + upstream error-text echoes via last_error). The dispatch
    handler enforces this via STRICT_SYSTEM_ONLY_TOOLS — verify the
    constant is hard-coded into the source rather than relying on a
    convention-only READ_TOOLS membership.
    """
    import inspect
    src = inspect.getsource(sse)
    assert "STRICT_SYSTEM_ONLY_TOOLS" in src, (
        "STRICT_SYSTEM_ONLY_TOOLS gate must be present"
    )
    assert "\"outbox_status\"" in src or "'outbox_status'" in src
    # Source must contain the actual deny path — not just the constant.
    assert "STRICT_SYSTEM_ONLY_TOOLS and not auth.is_system" in src, (
        "STRICT_SYSTEM_ONLY_TOOLS must be wired to a deny return path"
    )
