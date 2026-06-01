from __future__ import annotations

import json

from sos.bus import tenant_agent_mint as tam


def test_custom_agent_token_mint_honors_sos_bus_tokens_path(tmp_path, monkeypatch):
    tokens_path = tmp_path / "runtime" / "tokens.json"
    monkeypatch.setenv("SOS_BUS_TOKENS_PATH", str(tokens_path))

    raw, token_hash, minted = tam.mint_or_get_custom_tenant_agent_token("helper", "acme")

    assert minted is True
    assert raw.startswith("sk-helper-")
    assert token_hash
    records = json.loads(tokens_path.read_text())
    assert records[0]["agent"] == "helper"
    assert records[0]["tenant_slug"] == "acme"
    assert records[0]["agent_kind"] == "custom"


def test_custom_agent_qnft_mint_honors_sos_qnft_path(tmp_path, monkeypatch):
    qnft_path = tmp_path / "runtime" / "qnft_registry.json"
    monkeypatch.setenv("SOS_QNFT_PATH", str(qnft_path))

    record, minted = tam.mint_or_get_custom_qnft("helper", "acme", "claude-sonnet-4-6", "Ops")

    assert minted is True
    assert record["seed_hex"]
    registry = json.loads(qnft_path.read_text())
    assert registry["custom:acme:helper"]["agent_name"] == "helper"


# -----------------------------------------------------------------------
# S156 regression: newly-minted D-3b tokens must carry full scopes + permissions
# so the MCP dispatcher gate does not return 0 tools to the new agent.
# -----------------------------------------------------------------------

def test_custom_agent_token_mint_carries_full_scopes(tmp_path, monkeypatch):
    """S156 — scopes must include bus:read + health, not just bus:send."""
    tokens_path = tmp_path / "runtime" / "tokens.json"
    monkeypatch.setenv("SOS_BUS_TOKENS_PATH", str(tokens_path))

    _raw, _hash, minted = tam.mint_or_get_custom_tenant_agent_token("bot-acme", "acme")

    assert minted is True
    records = json.loads(tokens_path.read_text())
    entry = records[0]
    scopes = entry.get("scopes", [])
    assert "bus:send" in scopes, "bus:send must be present"
    assert "bus:read" in scopes, "S156: bus:read must be present for inbox/peers tools"
    assert "health" in scopes, "S156: health must be present for boot_context/status tools"


_STANDARD_AGENT_PERMISSIONS = frozenset([
    "bus:send", "bus:read", "health",
    "memory:*", "tasks:*",
    "skills:read", "skills:invoke",
])


def test_custom_agent_token_mint_carries_permissions(tmp_path, monkeypatch):
    """S156-harden — permissions must be the explicit least-privilege allowlist, not mcp:*."""
    tokens_path = tmp_path / "runtime" / "tokens.json"
    monkeypatch.setenv("SOS_BUS_TOKENS_PATH", str(tokens_path))

    _raw, _hash, minted = tam.mint_or_get_custom_tenant_agent_token("bot-acme2", "acme")

    assert minted is True
    records = json.loads(tokens_path.read_text())
    entry = records[0]
    permissions = entry.get("permissions")
    assert permissions is not None, "S156: permissions must not be null/missing"
    assert len(permissions) > 0, "S156: permissions list must not be empty"
    # No wildcard — new tools are opt-in, not auto-granted
    assert "mcp:*" not in permissions, "S156-harden: mcp:* wildcard must not be present"
    assert "*" not in permissions, "S156-harden: bare wildcard must not be present"
    # Explicit standard-agent allowlist must be present in full
    granted = frozenset(permissions)
    missing = _STANDARD_AGENT_PERMISSIONS - granted
    assert not missing, f"S156-harden: missing standard-agent permissions: {missing}"


def test_bus_state_lock_defaults_next_to_env_tokens_path(tmp_path, monkeypatch):
    tokens_path = tmp_path / "runtime" / "tokens.json"
    monkeypatch.setenv("SOS_BUS_TOKENS_PATH", str(tokens_path))

    with tam._bus_state_lock():
        pass

    assert (tokens_path.parent / ".tenant_mint.lock").exists()

