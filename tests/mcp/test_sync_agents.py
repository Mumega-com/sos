"""#161 — sync_agents MCP tool unit tests.

Tests:
  (a) Fresh tenant: 2 desired agents → both minted (agents_created len==2)
  (b) Re-run: both already exist → both in agents_existing, zero new mints
  (c) Squad join: desired squad → workspace_join called, squads_joined populated
  (d) Cross-tenant isolation: tenant token + different tenant_slug → rejected
  (e) Raw token never in the report output
  (f) dry_run: mints nothing, reports would_create

All mint/registry/workspace deps are mocked — no live Redis or file I/O.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub mirror modules (not on test path) before importing sos_mcp_sse
# ---------------------------------------------------------------------------
_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda _text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

from sos.mcp.sos_mcp_sse import (  # noqa: E402
    MCPAuthContext,
    _handle_sync_agents,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TENANT = "acme"
_ALT_TENANT = "rival"
_ALLOWED_MODEL = "claude-sonnet-4-6"


def _tenant_auth(tenant: str = _TENANT) -> MCPAuthContext:
    """Simulate a tenant-admin token scoped to `tenant`."""
    return MCPAuthContext(
        token=f"sk-test-{tenant}-token",
        tenant_id=tenant,
        tenant_slug=tenant,
        is_system=False,
        source="bus_tokens",
        agent_name="operator",
        scope="tenant",
        permissions=["agents:*", "workspace:*"],
    )


def _system_auth() -> MCPAuthContext:
    """Simulate a system/operator token (no tenant scope)."""
    return MCPAuthContext(
        token="sk-system-internal",
        tenant_id=None,
        is_system=True,
        source="env",
        agent_name="kasra",
        scope="",
        permissions=None,
    )


def _redis_stub() -> Any:
    """Minimal async Redis stub for workspace_join calls."""

    class _RedisStub:
        def __init__(self) -> None:
            self._sets: dict[str, set[str]] = {}
            self._hashes: dict[str, dict[str, str]] = {}
            self._counters: dict[str, int] = {}

        async def incr(self, key: str) -> int:
            self._counters[key] = self._counters.get(key, 0) + 1
            return self._counters[key]

        async def expire(self, _key: str, _ttl: int) -> bool:
            return True

        async def sadd(self, key: str, member: str) -> int:
            self._sets.setdefault(key, set()).add(member)
            return 1

        async def hset(self, key: str, mapping: dict[str, str]) -> int:
            self._hashes[key] = dict(mapping)
            return len(mapping)

        async def hgetall(self, key: str) -> dict[str, str]:
            return dict(self._hashes.get(key, {}))

    return _RedisStub()


def _make_token_record(agent_name: str, tenant_slug: str) -> dict[str, Any]:
    return {
        "token": f"sk-{agent_name}-abcdef12",
        "token_hash": "a" * 64,
        "agent": agent_name,
        "scope": "tenant-agent",
        "agent_kind": "custom",
        "tenant_slug": tenant_slug,
        "active": True,
    }


# ---------------------------------------------------------------------------
# Shared mint + workspace patch helpers
# ---------------------------------------------------------------------------

def _make_mint_patches(existing_tokens: list[dict[str, Any]], raw_token_base: str = "sk-new-abcdef12"):
    """Return a context-manager-compatible dict of patches for the mint path."""
    qnft_record = {
        "seed_hex": "deadbeef" * 8,
        "minted_at": "2026-06-01T00:00:00Z",
        "active": True,
    }

    def _fake_load_tokens():
        return list(existing_tokens)

    def _fake_mint_qnft(agent_name, tenant_slug, model, role):
        return qnft_record, True  # (record, minted=True)

    def _fake_mint_token(agent_name, tenant_slug):
        raw = f"{raw_token_base}-{agent_name}"
        return raw, "b" * 64, True  # (raw_token, token_hash, minted=True)

    def _fake_register_routing(agent_name, tenant_slug, routing="tenant-bus"):
        return True

    def _fake_scaffold(agent_name, tenant_slug, role, model, charter, voice_rules, qnft_seed_hex, mint_date):
        from pathlib import Path
        return Path(f"/tmp/fake/{tenant_slug}/{agent_name}/CLAUDE.md"), True

    return (
        _fake_load_tokens,
        _fake_mint_qnft,
        _fake_mint_token,
        _fake_register_routing,
        _fake_scaffold,
    )


def _apply_mint_patches(monkeypatch, existing_tokens: list[dict[str, Any]], redis_inst: Any) -> None:
    """Monkeypatch mint primitives, token loader, Redis, and publish_log."""
    (
        _fake_load_tokens,
        _fake_mint_qnft,
        _fake_mint_token,
        _fake_register_routing,
        _fake_scaffold,
    ) = _make_mint_patches(existing_tokens)

    mint_mod = "sos.bus.tenant_agent_mint"
    activation_mod = "sos.bus.tenant_agent_activation"

    monkeypatch.setattr(f"{mint_mod}.mint_or_get_custom_qnft", _fake_mint_qnft)
    monkeypatch.setattr(f"{mint_mod}.mint_or_get_custom_tenant_agent_token", _fake_mint_token)
    monkeypatch.setattr(f"{mint_mod}.register_or_skip_routing", _fake_register_routing)
    monkeypatch.setattr(f"{mint_mod}.scaffold_or_skip_custom_agent", _fake_scaffold)
    monkeypatch.setattr(f"{activation_mod}._load_tokens", _fake_load_tokens)

    from sos.mcp import sos_mcp_sse as module
    monkeypatch.setattr(module, "_get_redis", lambda: redis_inst)
    monkeypatch.setattr(module, "_publish_log", _noop_log)

    # Stub _local_token_cache.invalidate
    class _StubCache:
        def get(self):
            return {}
        def invalidate(self):
            pass
    monkeypatch.setattr(module, "_local_token_cache", _StubCache())

    # Stub _enforce_rate_limit to no-op
    monkeypatch.setattr(module, "_enforce_rate_limit", lambda _auth: None)


async def _noop_log(*_a: Any, **_kw: Any) -> None:
    pass


def _extract_result(response: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON result from _json_result / structuredContent."""
    if "structuredContent" in response:
        return response["structuredContent"]
    text = response["content"][0]["text"]
    return json.loads(text)


# ---------------------------------------------------------------------------
# (a) Fresh tenant: 2 desired agents → both minted
# ---------------------------------------------------------------------------

async def test_sync_agents_fresh_tenant_mints_both(monkeypatch):
    redis = _redis_stub()
    _apply_mint_patches(monkeypatch, existing_tokens=[], redis_inst=redis)

    auth = _tenant_auth()
    args = {
        "desired_agents": [
            {"name": "bot-one", "role": "executor", "model": _ALLOWED_MODEL},
            {"name": "bot-two", "role": "researcher", "model": _ALLOWED_MODEL},
        ],
    }
    response = await _handle_sync_agents(args, auth, session_id=None)
    result = _extract_result(response)

    assert result["tenant"] == _TENANT
    assert result["dry_run"] is False
    assert len(result["agents_created"]) == 2
    assert len(result["agents_existing"]) == 0
    assert result["errors"] == []

    names_created = {a["name"] for a in result["agents_created"]}
    assert names_created == {"bot-one", "bot-two"}

    for agent in result["agents_created"]:
        assert agent["status"] == "created"
        # token_tail must be present and short
        assert "token_tail" in agent
        assert len(agent["token_tail"]) <= 8


# ---------------------------------------------------------------------------
# (b) Re-run: both already exist → both existing, zero mints
# ---------------------------------------------------------------------------

async def test_sync_agents_idempotent_existing(monkeypatch):
    existing = [
        _make_token_record("bot-one", _TENANT),
        _make_token_record("bot-two", _TENANT),
    ]
    redis = _redis_stub()
    _apply_mint_patches(monkeypatch, existing_tokens=existing, redis_inst=redis)

    # Track whether mint was called
    mint_calls: list[str] = []

    def _tracked_mint_qnft(agent_name, tenant_slug, model, role):
        mint_calls.append(agent_name)
        qnft_record = {"seed_hex": "dead" * 16, "minted_at": "2026-06-01T00:00:00Z"}
        return qnft_record, False

    monkeypatch.setattr("sos.bus.tenant_agent_mint.mint_or_get_custom_qnft", _tracked_mint_qnft)

    auth = _tenant_auth()
    args = {
        "desired_agents": [
            {"name": "bot-one"},
            {"name": "bot-two"},
        ],
    }
    response = await _handle_sync_agents(args, auth, session_id=None)
    result = _extract_result(response)

    assert len(result["agents_existing"]) == 2
    assert len(result["agents_created"]) == 0
    assert result["errors"] == []
    # mint was not called for any agent
    assert mint_calls == []


# ---------------------------------------------------------------------------
# (c) Desired squad → workspace_join called, squads_joined populated
# ---------------------------------------------------------------------------

async def test_sync_agents_joins_squad(monkeypatch):
    redis = _redis_stub()
    _apply_mint_patches(monkeypatch, existing_tokens=[], redis_inst=redis)

    auth = _tenant_auth()
    args = {
        "desired_agents": [{"name": "bot-three", "role": "executor", "model": _ALLOWED_MODEL}],
        "squads": ["dev-squad"],
    }
    response = await _handle_sync_agents(args, auth, session_id=None)
    result = _extract_result(response)

    assert result["errors"] == []
    assert len(result["squads_joined"]) == 1
    assert result["squads_joined"][0]["workspace_id"] == "dev-squad"
    assert result["squads_joined"][0]["status"] == "joined"


# ---------------------------------------------------------------------------
# (d) Cross-tenant isolation: tenant token targeting different tenant → rejected
# ---------------------------------------------------------------------------

async def test_sync_agents_cross_tenant_rejected(monkeypatch):
    redis = _redis_stub()
    _apply_mint_patches(monkeypatch, existing_tokens=[], redis_inst=redis)

    auth = _tenant_auth(tenant=_TENANT)  # token scoped to "acme"
    args = {
        "desired_agents": [{"name": "bot-evil", "model": _ALLOWED_MODEL}],
        "tenant_slug": _ALT_TENANT,  # <-- trying to target "rival"
    }
    response = await _handle_sync_agents(args, auth, session_id=None)
    # Must return error text, not a structured result
    text = response["content"][0]["text"]
    assert "cross-tenant sync rejected" in text.lower() or "cross-tenant" in text
    assert _TENANT in text
    assert _ALT_TENANT in text


# ---------------------------------------------------------------------------
# (e) Raw token never appears in the report output
# ---------------------------------------------------------------------------

async def test_sync_agents_no_raw_token_in_output(monkeypatch):
    raw_token = "sk-new-abcdef12-bot-four"
    redis = _redis_stub()

    (
        _fake_load_tokens,
        _fake_mint_qnft,
        _,
        _fake_register_routing,
        _fake_scaffold,
    ) = _make_mint_patches(existing_tokens=[], raw_token_base="sk-new-abcdef12")

    def _mint_token_returns_known_raw(agent_name, tenant_slug):
        # Return a specific raw token so we can assert it's not in output
        return raw_token, "c" * 64, True

    monkeypatch.setattr("sos.bus.tenant_agent_mint.mint_or_get_custom_qnft", _fake_mint_qnft)
    monkeypatch.setattr("sos.bus.tenant_agent_mint.mint_or_get_custom_tenant_agent_token", _mint_token_returns_known_raw)
    monkeypatch.setattr("sos.bus.tenant_agent_mint.register_or_skip_routing", _fake_register_routing)
    monkeypatch.setattr("sos.bus.tenant_agent_mint.scaffold_or_skip_custom_agent", _fake_scaffold)
    monkeypatch.setattr("sos.bus.tenant_agent_activation._load_tokens", _fake_load_tokens)

    from sos.mcp import sos_mcp_sse as module
    monkeypatch.setattr(module, "_get_redis", lambda: redis)
    monkeypatch.setattr(module, "_publish_log", _noop_log)
    monkeypatch.setattr(module, "_enforce_rate_limit", lambda _auth: None)

    class _StubCache:
        def get(self): return {}
        def invalidate(self): pass
    monkeypatch.setattr(module, "_local_token_cache", _StubCache())

    auth = _tenant_auth()
    args = {"desired_agents": [{"name": "bot-four", "model": _ALLOWED_MODEL}]}
    response = await _handle_sync_agents(args, auth, session_id=None)

    # Serialize full response to string and verify raw token absent
    response_str = json.dumps(response)
    assert raw_token not in response_str, (
        f"Raw token '{raw_token}' found in response output — token leak!"
    )
    # Verify only the tail is present
    tail = raw_token[-8:]
    assert tail in response_str


# ---------------------------------------------------------------------------
# (f) dry_run: mints nothing, reports would_create
# ---------------------------------------------------------------------------

async def test_sync_agents_dry_run_no_minting(monkeypatch):
    redis = _redis_stub()
    _apply_mint_patches(monkeypatch, existing_tokens=[], redis_inst=redis)

    mint_called: list[str] = []

    def _fail_if_called(agent_name, tenant_slug, model, role):
        mint_called.append(agent_name)
        raise AssertionError(f"mint_or_get_custom_qnft called in dry_run for {agent_name}")

    monkeypatch.setattr("sos.bus.tenant_agent_mint.mint_or_get_custom_qnft", _fail_if_called)

    auth = _tenant_auth()
    args = {
        "desired_agents": [
            {"name": "dry-bot", "model": _ALLOWED_MODEL},
        ],
        "dry_run": True,
    }
    response = await _handle_sync_agents(args, auth, session_id=None)
    result = _extract_result(response)

    assert result["dry_run"] is True
    assert len(result["agents_created"]) == 1
    assert result["agents_created"][0]["status"] == "would_create"
    assert result["agents_created"][0]["dry_run"] is True
    assert mint_called == []  # no real mint happened
