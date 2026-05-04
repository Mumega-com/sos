"""S027 D-2 L-7 — bus-layer RLS three-discriminator enforcement.

Tests for `_enforce_tenant_agent_rls` in sos.mcp.sos_mcp_sse: when a sender's
token has scope='tenant-agent', the target peer must either share the same
tenant_slug or be a recognized substrate coordination agent. The
spoofed-tenant-slug-with-valid-agent-name attack vector is the load-bearing
invariant — verified directly.

Pair with sos/bus/tenant_agent_activation.py (D-2b) and bridge.py route. Together
these enforce: same-tenant-slug AND recognized-agent_kind AND recognized-agent_name
on every cross-agent send a tenant-agent token can initiate.
"""
from __future__ import annotations

import sys
import types

import pytest

# Mirror stubs (mirror modules not on test path).
_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

from fastapi import HTTPException

from sos.mcp.sos_mcp_sse import (
    MCPAuthContext,
    _enforce_tenant_agent_rls,
    _TENANT_AGENT_SUBSTRATE_PEERS,
)


def _tenant_agent_ctx(
    *, tenant_slug: str, agent_name: str, agent_kind: str
) -> MCPAuthContext:
    return MCPAuthContext(
        token="x" * 64,
        tenant_id=tenant_slug,
        is_system=False,
        source="test",
        agent_name=agent_name,
        scope="tenant-agent",
        agent_kind=agent_kind,
    )


def _substrate_ctx(agent_name: str) -> MCPAuthContext:
    return MCPAuthContext(
        token="y" * 64,
        tenant_id="sos",
        is_system=False,
        source="test",
        agent_name=agent_name,
        scope="",  # substrate "agent" tokens have empty scope
    )


def _seed_token_cache(monkeypatch, entries: list[MCPAuthContext]) -> None:
    """Replace _local_token_cache.get() output with a fixed dict for the test."""
    from sos.mcp import sos_mcp_sse as module

    fake_cache = {f"hash_{i}": ctx for i, ctx in enumerate(entries)}

    class _StubCache:
        def get(self):
            return fake_cache

        def invalidate(self):
            pass

    monkeypatch.setattr(module, "_local_token_cache", _StubCache())


# ---------------------------------------------------------------------------
# Pass-through (non-tenant-agent senders are not restricted by L-7)
# ---------------------------------------------------------------------------


def test_substrate_sender_passes_through(monkeypatch):
    """Substrate (scope='') tokens are not restricted by L-7."""
    _seed_token_cache(monkeypatch, [])
    auth = _substrate_ctx("kasra")
    # Should not raise even for arbitrary target.
    _enforce_tenant_agent_rls(auth, "anything-goes")


def test_customer_sender_passes_through(monkeypatch):
    """External customer tokens (scope='customer') are not restricted by L-7."""
    _seed_token_cache(monkeypatch, [])
    auth = MCPAuthContext(
        token="z" * 64, tenant_id="acme", is_system=False,
        source="test", agent_name="acme", scope="customer",
    )
    _enforce_tenant_agent_rls(auth, "kasra")


# ---------------------------------------------------------------------------
# Same-scope sends — allowed
# ---------------------------------------------------------------------------


def test_tenant_agent_can_target_substrate_peer_by_name(monkeypatch):
    """tenant-agent sender → loom (substrate coordination peer) is allowed."""
    _seed_token_cache(monkeypatch, [])
    auth = _tenant_agent_ctx(
        tenant_slug="acme", agent_name="athena-acme", agent_kind="athena"
    )
    _enforce_tenant_agent_rls(auth, "loom")
    _enforce_tenant_agent_rls(auth, "mizan")


def test_tenant_agent_can_target_same_tenant_peer(monkeypatch):
    """athena-acme → kasra-acme allowed when both have tenant_slug=acme."""
    peer = _tenant_agent_ctx(
        tenant_slug="acme", agent_name="kasra-acme", agent_kind="kasra"
    )
    _seed_token_cache(monkeypatch, [peer])
    auth = _tenant_agent_ctx(
        tenant_slug="acme", agent_name="athena-acme", agent_kind="athena"
    )
    _enforce_tenant_agent_rls(auth, "kasra-acme")


# ---------------------------------------------------------------------------
# Cross-tenant attack — must reject
# ---------------------------------------------------------------------------


def test_spoofed_tenant_slug_with_valid_agent_name_rejected(monkeypatch):
    """The load-bearing case from §3.7: athena-acme tries to reach a real
    agent in a DIFFERENT tenant. Token's tenant_slug=acme but peer's
    tenant_slug=other → 403 cross_tenant_send_blocked.
    """
    peer = _tenant_agent_ctx(
        tenant_slug="other", agent_name="athena-other", agent_kind="athena"
    )
    _seed_token_cache(monkeypatch, [peer])
    auth = _tenant_agent_ctx(
        tenant_slug="acme", agent_name="athena-acme", agent_kind="athena"
    )
    with pytest.raises(HTTPException) as excinfo:
        _enforce_tenant_agent_rls(auth, "athena-other")
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "cross_tenant_send_blocked"


def test_unknown_peer_rejected(monkeypatch):
    """tenant-agent → name with no token entry and not in substrate allowlist
    must reject (403 tenant_agent_unknown_peer). Prevents phantom-target sends.
    """
    _seed_token_cache(monkeypatch, [])
    auth = _tenant_agent_ctx(
        tenant_slug="acme", agent_name="athena-acme", agent_kind="athena"
    )
    with pytest.raises(HTTPException) as excinfo:
        _enforce_tenant_agent_rls(auth, "phantom-agent-zzz")
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "tenant_agent_unknown_peer"


def test_missing_tenant_slug_on_token_rejected(monkeypatch):
    """A malformed tenant-agent token (no tenant_slug) cannot prove same-scope."""
    _seed_token_cache(monkeypatch, [])
    auth = MCPAuthContext(
        token="x" * 64, tenant_id=None, is_system=False, source="test",
        agent_name="athena-broken", scope="tenant-agent", agent_kind="athena",
    )
    with pytest.raises(HTTPException) as excinfo:
        _enforce_tenant_agent_rls(auth, "loom")
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "tenant_agent_token_missing_discriminators"


def test_missing_agent_kind_on_token_rejected(monkeypatch):
    """A tenant-agent token without agent_kind cannot prove three-discriminator."""
    _seed_token_cache(monkeypatch, [])
    auth = MCPAuthContext(
        token="x" * 64, tenant_id="acme", is_system=False, source="test",
        agent_name="athena-acme", scope="tenant-agent", agent_kind="",
    )
    with pytest.raises(HTTPException) as excinfo:
        _enforce_tenant_agent_rls(auth, "loom")
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "tenant_agent_token_missing_discriminators"


# ---------------------------------------------------------------------------
# Substrate-peer allowlist coverage
# ---------------------------------------------------------------------------


def test_substrate_peer_allowlist_includes_canonical_coordinators():
    """Sanity check: well-known substrate names are in the allowlist."""
    for name in ("loom", "athena", "kasra", "mizan", "river", "calliope"):
        assert name in _TENANT_AGENT_SUBSTRATE_PEERS, (
            f"{name} should be a substrate coordination peer"
        )


def test_substrate_peer_via_real_token_allowed(monkeypatch):
    """A peer whose token entry has scope='' (real substrate) but isn't on the
    name allowlist should still pass via the scope check (not the name check).
    """
    peer = _substrate_ctx("custom-substrate-name")
    _seed_token_cache(monkeypatch, [peer])
    auth = _tenant_agent_ctx(
        tenant_slug="acme", agent_name="athena-acme", agent_kind="athena"
    )
    # custom-substrate-name is NOT in _TENANT_AGENT_SUBSTRATE_PEERS, so it falls
    # through to peer-scope lookup → peer.scope == "" → allowed as substrate.
    _enforce_tenant_agent_rls(auth, "custom-substrate-name")
