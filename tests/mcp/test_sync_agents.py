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
    """Monkeypatch the canonical mint orchestrator, token loader, Redis, and publish_log.

    After the fix, _handle_sync_agents calls mint_tenant_custom_agent (the
    orchestrator) rather than sub-primitives directly.  We patch the orchestrator
    at the module level so existing tests continue to work without changes.
    """
    (
        _fake_load_tokens,
        _fake_mint_qnft,
        _fake_mint_token,
        _fake_register_routing,
        _fake_scaffold,
    ) = _make_mint_patches(existing_tokens)

    mint_mod = "sos.bus.tenant_agent_mint"
    activation_mod = "sos.bus.tenant_agent_activation"

    # Patch sub-primitives so that mint_tenant_custom_agent (if called through)
    # uses fakes — and also patch the orchestrator itself so validate_mint_body
    # and validate_actor_token_claims are bypassed in unit tests.
    monkeypatch.setattr(f"{mint_mod}.mint_or_get_custom_qnft", _fake_mint_qnft)
    monkeypatch.setattr(f"{mint_mod}.mint_or_get_custom_tenant_agent_token", _fake_mint_token)
    monkeypatch.setattr(f"{mint_mod}.register_or_skip_routing", _fake_register_routing)
    monkeypatch.setattr(f"{mint_mod}.scaffold_or_skip_custom_agent", _fake_scaffold)
    monkeypatch.setattr(f"{activation_mod}._load_tokens", _fake_load_tokens)

    # Patch the orchestrator to skip validate_mint_body / actor-claim checks.
    # The orchestrator return shape must match what _handle_sync_agents reads:
    # {agent_name, token_hash, scaffold_path, idempotency: {qnft_minted, token_minted,
    #  routing_registered, scaffold_created}}
    def _fake_orchestrator(body: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path
        agent_name = body["agent_name"]
        tenant_slug = body["tenant_slug"]
        qnft_record, qnft_minted = _fake_mint_qnft(
            agent_name, tenant_slug, body["model"], body["role"]
        )
        _raw, token_hash, token_minted = _fake_mint_token(agent_name, tenant_slug)
        routing = _fake_register_routing(agent_name, tenant_slug)
        scaffold_path, scaffold_created = _fake_scaffold(
            agent_name, tenant_slug, body["role"], body["model"],
            body["charter"], body["voice_rules"],
            qnft_record["seed_hex"], qnft_record["minted_at"],
        )
        return {
            "tenant_id": body.get("tenant_id", tenant_slug),
            "tenant_slug": tenant_slug,
            "agent_name": agent_name,
            "agent_kind": agent_name,
            "qnft_seed_hex": qnft_record["seed_hex"],
            "token_hash": token_hash,
            "scaffold_path": str(scaffold_path),
            "tier": "tenant-custom",
            "signer": "tenant-admin",
            "model": body["model"],
            "role": body["role"],
            "idempotency": {
                "qnft_minted": qnft_minted,
                "token_minted": token_minted,
                "routing_registered": routing,
                "scaffold_created": scaffold_created,
            },
        }

    monkeypatch.setattr(f"{mint_mod}.mint_tenant_custom_agent", _fake_orchestrator)

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

    # Track whether the orchestrator was called — it must NOT be called for
    # agents that already exist (the existence check short-circuits before mint).
    orchestrator_calls: list[str] = []
    _original_fake = None

    import sos.bus.tenant_agent_mint as _tam_mod
    _original_fake = _tam_mod.mint_tenant_custom_agent

    def _tracked_orchestrator(body: dict[str, Any]) -> dict[str, Any]:
        orchestrator_calls.append(body["agent_name"])
        return _original_fake(body)

    monkeypatch.setattr("sos.bus.tenant_agent_mint.mint_tenant_custom_agent", _tracked_orchestrator)

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
    # orchestrator was not called for any agent — existence check short-circuits
    assert orchestrator_calls == []


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
    """Raw token must never appear in output; only token_hash tail is surfaced.

    After the fix the orchestrator returns token_hash (not raw_token), so the
    redacted tail is the last-8 chars of the hash — raw_token never leaves the
    sub-primitive.  We verify the raw token does NOT appear in the response.
    """
    raw_token = "sk-new-abcdef12-bot-four"
    known_hash = "c" * 64
    redis = _redis_stub()

    (
        _fake_load_tokens,
        _fake_mint_qnft,
        _,
        _fake_register_routing,
        _fake_scaffold,
    ) = _make_mint_patches(existing_tokens=[], raw_token_base="sk-new-abcdef12")

    def _mint_token_returns_known_raw(agent_name, tenant_slug):
        # raw_token intentionally not returned by orchestrator; captured here only
        # to confirm it never leaks.
        return raw_token, known_hash, True

    def _fake_orchestrator(body: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path
        agent_name = body["agent_name"]
        tenant_slug = body["tenant_slug"]
        qnft_record, qnft_minted = _fake_mint_qnft(
            agent_name, tenant_slug, body["model"], body["role"]
        )
        # Use our raw-token-returning sub-primitive to exercise the path.
        _raw, token_hash, token_minted = _mint_token_returns_known_raw(agent_name, tenant_slug)
        scaffold_path, scaffold_created = _fake_scaffold(
            agent_name, tenant_slug, body["role"], body["model"],
            body["charter"], body["voice_rules"],
            qnft_record["seed_hex"], qnft_record["minted_at"],
        )
        # Orchestrator returns token_hash, NOT raw_token.
        return {
            "tenant_id": body.get("tenant_id", tenant_slug),
            "tenant_slug": tenant_slug,
            "agent_name": agent_name,
            "agent_kind": agent_name,
            "qnft_seed_hex": qnft_record["seed_hex"],
            "token_hash": token_hash,
            "scaffold_path": str(scaffold_path),
            "tier": "tenant-custom",
            "signer": "tenant-admin",
            "model": body["model"],
            "role": body["role"],
            "idempotency": {
                "qnft_minted": qnft_minted,
                "token_minted": token_minted,
                "routing_registered": True,
                "scaffold_created": scaffold_created,
            },
        }

    monkeypatch.setattr("sos.bus.tenant_agent_mint.mint_tenant_custom_agent", _fake_orchestrator)
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
    # Verify only the hash tail is present (last-8 of known_hash = "cccccccc")
    tail = known_hash[-8:]
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


# ---------------------------------------------------------------------------
# (g) FIX 1 — reserved-name bypass: kasra/admin must be per-item error
# ---------------------------------------------------------------------------

async def test_sync_agents_reserved_name_is_per_item_error(monkeypatch):
    """Reserved names (kasra, admin, loom, …) must be rejected as a per-item
    error — NOT minted — and valid agents in the same call must still be minted.

    This exercises FIX 1: the canonical orchestrator's validate_mint_body raises
    ProvisionError(422, 'reserved_name', ...) which _handle_sync_agents catches
    and records without aborting the rest of the run.
    """
    redis = _redis_stub()

    # Use the REAL validate_mint_body / mint_tenant_custom_agent so the reserved-
    # name guard fires naturally — but stub the sub-primitives so no file I/O occurs.
    from sos.bus.tenant_agent_mint import ALLOWED_MODELS as _AM
    from sos.bus import tenant_agent_mint as _tam

    qnft_record = {"seed_hex": "ab" * 32, "minted_at": "2026-06-01T00:00:00Z"}

    def _fake_qnft(agent_name, tenant_slug, model, role):
        return qnft_record, True

    def _fake_token(agent_name, tenant_slug):
        return f"sk-{agent_name}-fake12", "d" * 64, True

    def _fake_routing(agent_name, tenant_slug, routing="tenant-bus"):
        return True

    def _fake_scaffold(agent_name, tenant_slug, role, model, charter, voice_rules, qnft_seed_hex, mint_date):
        from pathlib import Path
        return Path(f"/tmp/fake/{tenant_slug}/{agent_name}/CLAUDE.md"), True

    monkeypatch.setattr(_tam, "mint_or_get_custom_qnft", _fake_qnft)
    monkeypatch.setattr(_tam, "mint_or_get_custom_tenant_agent_token", _fake_token)
    monkeypatch.setattr(_tam, "register_or_skip_routing", _fake_routing)
    monkeypatch.setattr(_tam, "scaffold_or_skip_custom_agent", _fake_scaffold)

    # Also stub _bus_state_lock to a no-op so no flock is attempted in tests.
    import contextlib
    @contextlib.contextmanager
    def _noop_lock():
        yield
    monkeypatch.setattr(_tam, "_bus_state_lock", _noop_lock)

    # Stub token loader + Redis + cache in the MCP module.
    from sos.bus.tenant_agent_activation import _load_tokens as _real_load_tokens
    monkeypatch.setattr("sos.bus.tenant_agent_activation._load_tokens", lambda: [])

    from sos.mcp import sos_mcp_sse as module
    monkeypatch.setattr(module, "_get_redis", lambda: redis)
    monkeypatch.setattr(module, "_publish_log", _noop_log)
    monkeypatch.setattr(module, "_enforce_rate_limit", lambda _auth: None)

    class _StubCache:
        def get(self): return {}
        def invalidate(self): pass
    monkeypatch.setattr(module, "_local_token_cache", _StubCache())

    auth = _tenant_auth()
    args = {
        "desired_agents": [
            {"name": "kasra", "model": _ALLOWED_MODEL},   # reserved — must error
            {"name": "admin", "model": _ALLOWED_MODEL},   # reserved — must error
            {"name": "loom-1", "model": _ALLOWED_MODEL},  # reserved prefix — must error
            {"name": "valid-bot", "model": _ALLOWED_MODEL},  # valid — must be minted
        ],
    }
    response = await _handle_sync_agents(args, auth, session_id=None)
    result = _extract_result(response)

    # Three reserved-name entries → per-item errors
    assert len(result["errors"]) == 3
    error_agents = {e["agent"] for e in result["errors"]}
    assert "kasra" in error_agents
    assert "admin" in error_agents
    assert "loom-1" in error_agents

    # All errors must mention reserved_name
    for err in result["errors"]:
        assert "reserved_name" in err["error"], (
            f"Expected 'reserved_name' in error for {err['agent']!r}, got: {err['error']!r}"
        )

    # Valid agent must still be minted — run was not aborted
    assert len(result["agents_created"]) == 1
    assert result["agents_created"][0]["name"] == "valid-bot"
    assert result["agents_created"][0]["status"] == "created"


# ---------------------------------------------------------------------------
# (h) FIX 3 — cap: >25 desired_agents → rejected before any mint
# ---------------------------------------------------------------------------

async def test_sync_agents_cap_rejects_oversized_list(monkeypatch):
    """>25 desired_agents must be rejected before any minting occurs."""
    redis = _redis_stub()
    _apply_mint_patches(monkeypatch, existing_tokens=[], redis_inst=redis)

    orchestrator_called: list[str] = []

    import sos.bus.tenant_agent_mint as _tam
    _original = _tam.mint_tenant_custom_agent

    def _tracked(body: dict[str, Any]) -> dict[str, Any]:
        orchestrator_called.append(body["agent_name"])
        return _original(body)

    monkeypatch.setattr("sos.bus.tenant_agent_mint.mint_tenant_custom_agent", _tracked)

    auth = _tenant_auth()
    # Build 26 valid agent names (exceeds the 25-item cap)
    oversized = [{"name": f"bot-{i:02d}", "model": _ALLOWED_MODEL} for i in range(26)]
    args = {"desired_agents": oversized}

    response = await _handle_sync_agents(args, auth, session_id=None)

    # Must be an error text response (not structured result)
    text = response["content"][0]["text"]
    assert "Error" in text
    assert "25" in text, f"Expected cap size 25 in error message, got: {text!r}"
    assert "26" in text, f"Expected actual count 26 in error message, got: {text!r}"

    # Orchestrator must never have been called — rejection is pre-mint
    assert orchestrator_called == [], (
        f"Orchestrator was called despite oversized list: {orchestrator_called}"
    )
