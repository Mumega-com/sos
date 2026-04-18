"""
sos.services.registry — canonical agent registry read/write API.

All consumers MUST use this module instead of reading sos:registry:* redis
hashes directly. This guarantees every agent entry is deserialized through
AgentIdentity so the shape is always typed and consistent.

Redis key format: sos:registry[:<project>]:<agent_name_or_id>

v0.7.2 adds a second, parallel keyspace for runtime AgentCards:

    sos:cards[:<project>]:<agent_name>

AgentCard is the operational overlay (session, pid, host, warm_policy,
last_seen, cache_ttl_s, plan) that Inkwell and other operator UIs need
to render *which* agent is alive *right now* on *what* host. The
soul-level AgentIdentity keyspace above is unchanged — cards reference
their identity via ``identity_id`` (pattern ``agent:<slug>``).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from sos.contracts.agent_card import AgentCard
from sos.kernel.identity import (
    AgentDNA,
    AgentEconomics,
    AgentIdentity,
    Identity,
    IdentityType,
    PhysicsState,
    VerificationStatus,
)

logger = logging.getLogger("sos.registry")

def _get_redis() -> Any:
    """Return a Redis client. Raises on connection failure (caller handles)."""
    import redis  # type: ignore[import-untyped]
    from sos.kernel.settings import get_settings as _get_settings

    _s = _get_settings().redis
    return redis.Redis(
        host=_s.host,
        port=_s.port,
        password=_s.password_str or None,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _key_prefix(project: str | None = None) -> str:
    if project:
        return f"sos:registry:{project}:"
    return "sos:registry:"


def _deserialize(data: dict[str, Any], key: str) -> Optional[AgentIdentity]:
    """
    Deserialize a redis HGETALL dict into an AgentIdentity.

    Tolerates old-shape hashes with only {name, model, status, last_seen}.
    Missing fields receive sane defaults. Returns None if the hash is empty
    or cannot be parsed at all.
    """
    if not data:
        return None

    # Derive agent name from the key (last segment) or the stored name field
    name = data.get("name") or key.split(":")[-1]
    if not name:
        return None

    # --- Build physics state from stored dna.physics.* or defaults ---
    physics_raw: dict[str, Any] = {}
    dna_raw = data.get("dna")
    if dna_raw:
        try:
            dna_dict = json.loads(dna_raw) if isinstance(dna_raw, str) else dna_raw
            physics_raw = dna_dict.get("physics", {}) if isinstance(dna_dict, dict) else {}
        except Exception:
            pass

    physics = PhysicsState(
        C=float(physics_raw.get("C", 0.95)),
        alpha_norm=float(physics_raw.get("alpha_norm", 0.0)),
        regime=physics_raw.get("regime", "stable"),
        inner=physics_raw.get(
            "inner", {"receptivity": 1.0, "will": 0.8, "logic": 0.9}
        ),
    )

    dna = AgentDNA(
        id=f"agent:{name}",
        name=name,
        physics=physics,
        learning_strategy=(
            json.loads(dna_raw).get("learning_strategy", "balanced")
            if dna_raw and isinstance(dna_raw, str)
            else "balanced"
        ),
    )

    # --- Verification status ---
    raw_vs = data.get("verification_status", "unverified")
    try:
        vs = VerificationStatus(raw_vs)
    except ValueError:
        vs = VerificationStatus.UNVERIFIED

    # --- Build AgentIdentity ---
    ident = AgentIdentity(
        name=name,
        model=data.get("model"),
        squad_id=data.get("squad_id") or data.get("squad") or data.get("squads") or None,
        guild_id=data.get("guild_id"),
        public_key=data.get("public_key"),
        edition=data.get("edition", "business"),
        dna=dna,
    )
    ident.verification_status = vs
    ident.verified_by = data.get("verified_by")

    # Store runtime extras in metadata so templates can reach them
    ident.metadata["status"] = data.get("status", "unknown")
    ident.metadata["last_seen"] = data.get("last_seen", "")
    ident.metadata["role"] = data.get("role") or data.get("type", "")
    ident.metadata["project"] = data.get("project") or data.get("scope", "")
    ident.metadata["agent_type"] = data.get("agent_type") or data.get("type", "")

    # Capabilities: stored as JSON array or comma-separated string
    caps_raw = data.get("capabilities", "")
    if caps_raw:
        try:
            ident.capabilities = json.loads(caps_raw)
        except (json.JSONDecodeError, TypeError):
            ident.capabilities = [c.strip() for c in caps_raw.split(",") if c.strip()]

    return ident


def read_all(project: str | None = None) -> list[AgentIdentity]:
    """
    Scan sos:registry[:<project>]:* and deserialize every hash into AgentIdentity.

    Returns an empty list if redis is unreachable or no keys match.
    """
    try:
        r = _get_redis()
        prefix = _key_prefix(project)
        keys = r.keys(f"{prefix}*")
        agents: list[AgentIdentity] = []
        for key in keys:
            try:
                data = r.hgetall(key)
                ident = _deserialize(data, key)
                if ident is not None:
                    agents.append(ident)
            except Exception:
                logger.debug("Failed to deserialize registry key %s", key, exc_info=True)
        return agents
    except Exception:
        logger.debug("Redis unreachable in registry.read_all", exc_info=True)
        return []


def read_one(agent_id: str, project: str | None = None) -> Optional[AgentIdentity]:
    """
    Read a single agent from redis by agent_id (e.g. 'agent:kasra' or 'kasra').

    Returns None if not found or redis is unreachable.
    """
    name = agent_id.removeprefix("agent:")
    try:
        r = _get_redis()
        prefix = _key_prefix(project)
        key = f"{prefix}{name}"
        data = r.hgetall(key)
        return _deserialize(data, key)
    except Exception:
        logger.debug("Redis unreachable in registry.read_one", exc_info=True)
        return None


def write(
    ident: AgentIdentity,
    project: str | None = None,
    ttl_seconds: int = 300,
) -> None:
    """
    Serialize an AgentIdentity and HSET it to redis with a TTL.

    The wire format mirrors AgentIdentity.to_dict() so existing sos:registry
    entries continue to deserialize correctly (backward-compatible additions).
    """
    try:
        r = _get_redis()
        name = ident.name
        prefix = _key_prefix(project)
        key = f"{prefix}{name}"

        d = ident.to_dict()
        flat: dict[str, str] = {
            "name": name,
            "model": ident.model or "",
            "squad_id": ident.squad_id or "",
            "guild_id": ident.guild_id or "",
            "public_key": ident.public_key or "",
            "edition": ident.edition,
            "verification_status": ident.verification_status.value,
            "verified_by": ident.verified_by or "",
            "capabilities": json.dumps(ident.capabilities),
            "status": ident.metadata.get("status", ""),
            "last_seen": ident.metadata.get("last_seen", ""),
            "role": ident.metadata.get("role", ""),
            "project": ident.metadata.get("project", project or ""),
            "agent_type": ident.metadata.get("agent_type", ""),
        }
        if ident.dna:
            flat["dna"] = json.dumps(ident.dna.to_dict())

        r.hset(key, mapping=flat)
        if ttl_seconds > 0:
            r.expire(key, ttl_seconds)
    except Exception:
        logger.warning("Failed to write agent %s to registry", ident.name, exc_info=True)


# ---------------------------------------------------------------------------
# AgentCard helpers (runtime overlay) — v0.7.2
# ---------------------------------------------------------------------------
#
# Cards live under ``sos:cards[:<project>]:<agent_name>`` so they never
# collide with AgentIdentity hashes under ``sos:registry:``.  The contract
# (sos/contracts/agent_card.py) already defines to_redis_hash /
# from_redis_hash; this module just wires Redis I/O and scope handling
# around those helpers.


def _cards_key_prefix(project: str | None = None) -> str:
    if project:
        return f"sos:cards:{project}:"
    return "sos:cards:"


def read_all_cards(project: str | None = None) -> list[AgentCard]:
    """Scan ``sos:cards[:<project>]:*`` and return every parseable AgentCard.

    Returns an empty list if Redis is unreachable or no cards are
    registered — matches the fail-soft behaviour of ``read_all``.
    """
    try:
        r = _get_redis()
        prefix = _cards_key_prefix(project)
        keys = r.keys(f"{prefix}*")
        cards: list[AgentCard] = []
        for key in keys:
            try:
                data = r.hgetall(key)
                if not data:
                    continue
                cards.append(AgentCard.from_redis_hash(data))
            except Exception:
                logger.debug("Failed to parse card at %s", key, exc_info=True)
        return cards
    except Exception:
        logger.debug("Redis unreachable in registry.read_all_cards", exc_info=True)
        return []


def read_card(agent_name: str, project: str | None = None) -> Optional[AgentCard]:
    """Read one AgentCard by agent name (strips an ``agent:`` prefix)."""
    name = agent_name.removeprefix("agent:")
    try:
        r = _get_redis()
        key = f"{_cards_key_prefix(project)}{name}"
        data = r.hgetall(key)
        if not data:
            return None
        return AgentCard.from_redis_hash(data)
    except Exception:
        logger.debug("Redis unreachable in registry.read_card", exc_info=True)
        return None


def write_card(
    card: AgentCard,
    project: str | None = None,
    ttl_seconds: int = 300,
) -> None:
    """HSET an AgentCard under ``sos:cards[:<project>]:<card.name>`` with TTL.

    Cards are heartbeat-style state: the TTL defaults to 300s so stale
    entries expire on their own if an agent dies without cleanup. The
    ``project`` arg should match ``card.project`` when that is set;
    callers that mix the two get whatever they asked for, no enforcement.
    """
    try:
        r = _get_redis()
        key = f"{_cards_key_prefix(project)}{card.name}"
        r.hset(key, mapping=card.to_redis_hash())
        if ttl_seconds > 0:
            r.expire(key, ttl_seconds)
    except Exception:
        logger.warning("Failed to write card %s to registry", card.name, exc_info=True)
