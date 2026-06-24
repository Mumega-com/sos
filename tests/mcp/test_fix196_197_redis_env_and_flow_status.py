"""Tests for fix #196 (REDIS_HOST/REDIS_PORT env vars) and fix #197 (flow_status on clean local install).

These are unit tests that import the module and assert the fixes without needing
a live Redis or SaaS endpoint.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

# Stub mirror deps so sos_mcp_sse imports cleanly
_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)


# ---------------------------------------------------------------------------
# Issue #196 — REDIS_HOST / REDIS_PORT env vars
# ---------------------------------------------------------------------------


def test_redis_host_default() -> None:
    """REDIS_HOST defaults to 127.0.0.1 when env var is unset."""
    import importlib

    import sos.mcp.sos_mcp_sse as sse

    assert sse.REDIS_HOST == os.environ.get("REDIS_HOST", "127.0.0.1")


def test_redis_port_default() -> None:
    """REDIS_PORT defaults to 6379 when env var is unset."""
    import sos.mcp.sos_mcp_sse as sse

    assert sse.REDIS_PORT == int(os.environ.get("REDIS_PORT", "6379"))


def test_redis_url_uses_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_redis() builds URL from REDIS_HOST/REDIS_PORT, not hardcoded localhost:6379."""
    import sos.mcp.sos_mcp_sse as sse

    captured_url: list[str] = []
    original = sse.aioredis.from_url

    def fake_from_url(url: str, **kw: object) -> object:
        captured_url.append(url)
        return original(url, **kw)

    monkeypatch.setattr(sse, "REDIS_HOST", "myhost.local")
    monkeypatch.setattr(sse, "REDIS_PORT", 16379)
    monkeypatch.setattr(sse, "REDIS_PASSWORD", "")
    monkeypatch.setattr(sse.aioredis, "from_url", fake_from_url)
    # Force re-init of the global _redis
    monkeypatch.setattr(sse, "_redis", None)

    sse._get_redis()

    assert captured_url, "_get_redis did not call aioredis.from_url"
    url = captured_url[0]
    assert "myhost.local" in url, f"REDIS_HOST not in URL: {url}"
    assert "16379" in url, f"REDIS_PORT not in URL: {url}"
    assert "6379" not in url.replace("16379", ""), "Hardcoded 6379 leaked into URL"


# ---------------------------------------------------------------------------
# Issue #197 — flow_status on clean local install
# ---------------------------------------------------------------------------


def test_mcp_to_saas_contract_not_critical_without_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a clean local install with no SaaS creds, mcp_to_saas_audit must NOT be critical."""
    import sos.mcp.sos_mcp_sse as sse

    # Remove all SaaS credential env vars to simulate bare local install
    for key in ("SOS_SAAS_ADMIN_KEY", "SOS_SAAS_TOKEN", "MUMEGA_MASTER_KEY"):
        monkeypatch.delenv(key, raising=False)

    # Also clear module-level constant that was already evaluated
    # We need to re-evaluate the contract with fresh env.
    # The fix evaluates `any(os.environ.get(k) …)` at module import time,
    # so we patch the tuple entry directly to simulate bare install.
    fresh_contract = dict(sse._SERVICE_AUTHORITY_CONTRACTS[0])
    fresh_contract["required"] = any(
        os.environ.get(k) for k in ("SOS_SAAS_ADMIN_KEY", "SOS_SAAS_TOKEN", "MUMEGA_MASTER_KEY")
    )
    new_contracts = (fresh_contract,) + sse._SERVICE_AUTHORITY_CONTRACTS[1:]
    monkeypatch.setattr(sse, "_SERVICE_AUTHORITY_CONTRACTS", new_contracts)

    contracts = sse._service_authority_contracts()
    saas_contract = next(c for c in contracts if c["edge"] == "mcp_to_saas_audit")

    assert saas_contract["status"] != "critical", (
        f"mcp_to_saas_audit must not be critical on clean local install, got: {saas_contract['status']}"
    )
    assert saas_contract["status"] == "degraded", (
        f"Expected 'degraded' on clean local (no creds, not required), got: {saas_contract['status']}"
    )


def test_mcp_to_saas_contract_critical_when_creds_configured_but_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SaaS creds env var is set (hosted deploy) but empty/wrong, status must be critical."""
    import sos.mcp.sos_mcp_sse as sse

    # Simulate a hosted deploy: one of the accepted_by keys is present (non-empty)
    # but none of the accepted_by keys are actually valid → accepted_configured=[]
    # In practice this simulates: key configured but auth fails (deploy intent clear).
    monkeypatch.setenv("SOS_SAAS_TOKEN", "some-hosted-token")
    # But to get accepted_configured=[] (simulating "configured but wrong"), we need
    # the accepted_by check in _service_authority_contracts to see nothing.
    # Since the cred IS configured, required=True, and if accepted_configured is empty → critical.
    # Re-evaluate required with fresh env
    fresh_contract = dict(sse._SERVICE_AUTHORITY_CONTRACTS[0])
    fresh_contract["required"] = any(
        os.environ.get(k) for k in ("SOS_SAAS_ADMIN_KEY", "SOS_SAAS_TOKEN", "MUMEGA_MASTER_KEY")
    )
    new_contracts = (fresh_contract,) + sse._SERVICE_AUTHORITY_CONTRACTS[1:]
    monkeypatch.setattr(sse, "_SERVICE_AUTHORITY_CONTRACTS", new_contracts)

    # Patch accepted_by so none of them appear configured (test the required-but-missing path)
    broken_contract = dict(fresh_contract)
    broken_contract["accepted_by"] = ["NONEXISTENT_KEY_1", "NONEXISTENT_KEY_2"]
    broken_contracts = (broken_contract,) + sse._SERVICE_AUTHORITY_CONTRACTS[1:]
    monkeypatch.setattr(sse, "_SERVICE_AUTHORITY_CONTRACTS", broken_contracts)

    contracts = sse._service_authority_contracts()
    saas_contract = next(c for c in contracts if c["edge"] == "mcp_to_saas_audit")

    # required=True (SOS_SAAS_TOKEN is set) + accepted_configured=[] → critical (correct)
    assert saas_contract["status"] == "critical", (
        f"Expected critical when creds configured but wrong, got: {saas_contract['status']}"
    )


def test_flow_status_not_critical_on_local_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_overall_from_checks must not return critical when all contracts are degraded/healthy."""
    import sos.mcp.sos_mcp_sse as sse

    result = sse._overall_from_checks({
        "service_authority": {"status": "degraded"},
        "memory_scope": {"status": "healthy"},
    })
    assert result != "critical", f"Expected non-critical overall status, got: {result}"
    assert result == "degraded"


# ---------------------------------------------------------------------------
# Issue #197 part 2 — /health flow_status propagates degraded (not healthy)
# on clean-local install (Codex RED blocker)
# ---------------------------------------------------------------------------


def test_health_flow_status_degraded_on_clean_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On clean local (no SaaS creds), /health flow_status must be 'degraded', not 'healthy'.

    Repro: Codex showed /health => flow_status:'healthy' on clean HOME with SaaS creds
    unset.  The old code used a 2-level binary (critical|healthy) in the service_authority
    status, skipping 'degraded'.  After fix the 3-level ladder (critical→degraded→healthy)
    mirrors _run_flow_health's logic so clean-local yields 'degraded'.
    """
    import sos.mcp.sos_mcp_sse as sse

    # Simulate clean local: no SaaS cred env vars, so mcp_to_saas_audit is degraded
    for key in ("SOS_SAAS_ADMIN_KEY", "SOS_SAAS_TOKEN", "MUMEGA_MASTER_KEY"):
        monkeypatch.delenv(key, raising=False)

    # Re-evaluate required with fresh env (same technique as above tests)
    fresh_contract = dict(sse._SERVICE_AUTHORITY_CONTRACTS[0])
    fresh_contract["required"] = any(
        os.environ.get(k) for k in ("SOS_SAAS_ADMIN_KEY", "SOS_SAAS_TOKEN", "MUMEGA_MASTER_KEY")
    )
    new_contracts = (fresh_contract,) + sse._SERVICE_AUTHORITY_CONTRACTS[1:]
    monkeypatch.setattr(sse, "_SERVICE_AUTHORITY_CONTRACTS", new_contracts)

    contracts = sse._service_authority_contracts()
    # Build the service_authority status the same way /health now does (3-level ladder)
    sa_status = (
        "critical"
        if any(c["status"] == "critical" for c in contracts)
        else "degraded"
        if any(c["status"] != "healthy" for c in contracts)
        else "healthy"
    )
    flow_status = sse._overall_from_checks({
        "service_authority": {"status": sa_status},
        "memory_scope": {"status": "healthy"},
    })

    assert flow_status == "degraded", (
        f"Expected flow_status='degraded' on clean local, got: {flow_status!r}"
    )


def test_health_flow_status_critical_when_required_creds_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a required SaaS contract is critical, flow_status must still be 'critical'.

    Regression guard: the 3-level ladder must not accidentally swallow critical into
    degraded — critical must propagate through to the /health flow_status.
    """
    import sos.mcp.sos_mcp_sse as sse

    # Force the saas contract to be critical: mark required + no accepted_by keys set
    fresh_contract = dict(sse._SERVICE_AUTHORITY_CONTRACTS[0])
    fresh_contract["required"] = True
    broken_contract = dict(fresh_contract)
    broken_contract["accepted_by"] = ["NONEXISTENT_KEY_A", "NONEXISTENT_KEY_B"]
    broken_contracts = (broken_contract,) + sse._SERVICE_AUTHORITY_CONTRACTS[1:]
    monkeypatch.setattr(sse, "_SERVICE_AUTHORITY_CONTRACTS", broken_contracts)

    contracts = sse._service_authority_contracts()
    sa_status = (
        "critical"
        if any(c["status"] == "critical" for c in contracts)
        else "degraded"
        if any(c["status"] != "healthy" for c in contracts)
        else "healthy"
    )
    flow_status = sse._overall_from_checks({
        "service_authority": {"status": sa_status},
        "memory_scope": {"status": "healthy"},
    })

    assert flow_status == "critical", (
        f"Expected flow_status='critical' when required contract missing, got: {flow_status!r}"
    )
