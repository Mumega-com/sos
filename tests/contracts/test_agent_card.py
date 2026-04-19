"""Contract tests for the Agent Card schema.

These tests are the freeze point: if they pass, any implementation (Python,
Rust, TypeScript) that emits records passing them is wire-compatible.
"""

from __future__ import annotations

import pytest

from sos.contracts.agent_card import AgentCard, AGENT_CARD_VERSION


def _valid_card_kwargs() -> dict:
    return {
        "identity_id": "agent:sos-medic",
        "name": "sos-medic",
        "tool": "claude-code",
        "type": "tmux",
        "role": "medic",
        "model": "sonnet-4.6",
        "session": "sos-medic",
        "skills": ["connectivity", "auth", "routing"],
        "squads": ["core", "infrastructure"],
        "warm_policy": "cold",
        "cache_ttl_s": 300,
        "registered_at": AgentCard.now_iso(),
        "last_seen": AgentCard.now_iso(),
    }


def test_minimal_valid_card_roundtrips_through_redis_hash():
    card = AgentCard(**_valid_card_kwargs())
    h = card.to_redis_hash()

    # Redis hashes are flat strings — this is the invariant that matters
    assert all(isinstance(v, str) for v in h.values())

    # Arrays became comma-joined strings
    assert h["skills"] == "connectivity,auth,routing"

    # Null optional fields were dropped
    assert "project" not in h
    assert "tenant_subdomain" not in h

    restored = AgentCard.from_redis_hash(h)
    assert restored.name == card.name
    assert restored.skills == card.skills
    assert restored.squads == card.squads
    assert restored.agent_card_version == AGENT_CARD_VERSION


def test_invalid_name_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "name": "Bad Name With Spaces"})
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "name": "X"})  # too short


def test_unknown_tool_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "tool": "emacs"})


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "role": "boss"})


def test_duplicate_skill_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "skills": ["seo", "seo"]})


def test_cache_ttl_bounds():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "cache_ttl_s": -1})
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "cache_ttl_s": 86401})


def test_timestamp_must_be_iso():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "registered_at": "yesterday"})


def test_tenant_agent_card_shape():
    """The shape I registered for trop today should validate."""
    trop = AgentCard(
        identity_id="agent:trop",
        name="trop",
        tool="claude-code",
        type="tmux",
        role="executor",
        model="sonnet-4.6",
        session="trop",
        skills=["content", "growth-loops", "glass-commerce", "seo"],
        squads=["trop", "content"],
        project="trop",
        tenant_subdomain="trop.mumega.com",
        plan="growth",
        warm_policy="cold",
        cache_ttl_s=300,
        registered_at=AgentCard.now_iso(),
        last_seen=AgentCard.now_iso(),
    )
    h = trop.to_redis_hash()
    assert h["project"] == "trop"
    assert h["plan"] == "growth"
    assert h["tenant_subdomain"] == "trop.mumega.com"
    assert h["identity_id"] == "agent:trop"


def test_coordinator_agent_card_shape():
    """The shape I registered for kasra today should validate."""
    kasra = AgentCard(
        identity_id="agent:kasra",
        name="kasra",
        tool="claude-code",
        type="tmux",
        role="coordinator",
        model="sonnet-4.6",
        session="kasra",
        skills=["backend", "frontend", "infrastructure", "api", "database"],
        squads=["core", "engineering"],
        warm_policy="warm",
        cache_ttl_s=300,
        registered_at=AgentCard.now_iso(),
        last_seen=AgentCard.now_iso(),
    )
    assert kasra.role == "coordinator"
    assert kasra.warm_policy == "warm"
    assert kasra.identity_id == "agent:kasra"


def test_schema_file_parses():
    """The published JSON Schema must itself be valid JSON."""
    from sos.contracts.agent_card import load_schema

    schema = load_schema()
    assert schema["$id"].endswith("agent_card_v1.json")
    assert schema["type"] == "object"
    assert "name" in schema["required"]
    assert "identity_id" in schema["required"]


# ---- Island #3: identity_id + expanded type enum --------------------------


def test_identity_id_required():
    """AgentCard without identity_id must raise ValidationError."""
    import pytest

    kwargs = {k: v for k, v in _valid_card_kwargs().items() if k != "identity_id"}
    with pytest.raises(ValueError):
        AgentCard(**kwargs)


def test_identity_id_pattern_enforced():
    """identity_id must match ^agent:[a-z][a-z0-9-]*$."""
    import pytest

    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "identity_id": "user:river"})
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "identity_id": "agent:River"})
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "identity_id": "agent:"})
    # Valid pattern should pass
    card = AgentCard(**{**_valid_card_kwargs(), "identity_id": "agent:river-2"})
    assert card.identity_id == "agent:river-2"


def test_hermes_type_accepted():
    """type='hermes' must be a valid AgentCard type."""
    card = AgentCard(**{**_valid_card_kwargs(), "type": "hermes"})
    assert card.type == "hermes"


def test_codex_type_accepted():
    """type='codex' must be a valid AgentCard type."""
    card = AgentCard(**{**_valid_card_kwargs(), "type": "codex"})
    assert card.type == "codex"


def test_human_type_accepted():
    """type='human' supports mixed human+AI squad members per coherence plan."""
    card = AgentCard(**{**_valid_card_kwargs(), "type": "human", "role": "human"})
    assert card.type == "human"
    assert card.role == "human"


def test_resolve_identity_stub_raises():
    """resolve_identity() must raise NotImplementedError until registry is wired."""
    import pytest

    card = AgentCard(**_valid_card_kwargs())
    with pytest.raises(NotImplementedError):
        card.resolve_identity()


# ---- Phase 3 / W0: heartbeat_url field ------------------------------------


def test_heartbeat_url_none_roundtrips():
    """Card with heartbeat_url=None round-trips through Redis without errors."""
    card = AgentCard(**{**_valid_card_kwargs(), "heartbeat_url": None})
    assert card.heartbeat_url is None
    h = card.to_redis_hash()
    # None fields are omitted from the hash
    assert "heartbeat_url" not in h
    restored = AgentCard.from_redis_hash(h)
    assert restored.heartbeat_url is None


def test_heartbeat_url_set_roundtrips():
    """Card with a heartbeat_url value survives a Redis round-trip intact."""
    url = "https://example.invalid/hb"
    card = AgentCard(**{**_valid_card_kwargs(), "heartbeat_url": url})
    assert card.heartbeat_url == url
    h = card.to_redis_hash()
    assert h["heartbeat_url"] == url
    restored = AgentCard.from_redis_hash(h)
    assert restored.heartbeat_url == url


def test_heartbeat_url_empty_string_in_hash_becomes_none():
    """Empty string in the Redis hash round-trips back to None (no Redis null)."""
    h = AgentCard(**_valid_card_kwargs()).to_redis_hash()
    h["heartbeat_url"] = ""
    restored = AgentCard.from_redis_hash(h)
    assert restored.heartbeat_url is None


def test_existing_fields_intact_when_heartbeat_url_set():
    """Setting heartbeat_url does not disturb any existing AgentCard fields."""
    url = "https://example.invalid/hb"
    card = AgentCard(**{**_valid_card_kwargs(), "heartbeat_url": url})
    h = card.to_redis_hash()
    restored = AgentCard.from_redis_hash(h)
    assert restored.name == card.name
    assert restored.skills == card.skills
    assert restored.squads == card.squads
    assert restored.role == card.role
    assert restored.tool == card.tool
    assert restored.agent_card_version == AGENT_CARD_VERSION
    assert restored.heartbeat_url == url
