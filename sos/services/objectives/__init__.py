"""
sos.services.objectives — Redis-backed storage layer for the living objective tree.

All functions are fail-soft: RedisError is caught, a warning is logged, and a
sensible default (None / False / empty list) is returned. Never raises.

Redis key layout
----------------
  sos:objectives:{project}:{obj_id}          — HASH  (Objective fields)
  sos:objectives:{project}:children:{parent} — SET   (child IDs)
  sos:objectives:{project}:open              — SET   (IDs in state='open')

The ``project`` segment defaults to the literal string ``"default"`` when
project is None, so all keys are consistent-length and scannable per project.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sos.contracts.objective import Objective

logger = logging.getLogger("sos.objectives")


# ---------------------------------------------------------------------------
# Redis client — same lazy-import pattern as sos.services.registry
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC time as ISO-8601 with Z suffix — matches Objective.now_iso()."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _objectives_key_prefix(project: str | None) -> str:
    """Base prefix for all objective hashes in a project."""
    return f"sos:objectives:{project or 'default'}"


def _obj_key(obj_id: str, project: str | None) -> str:
    return f"{_objectives_key_prefix(project)}:{obj_id}"


def _children_set_key(parent_id: str, project: str | None) -> str:
    """Redis SET of child IDs for a given parent."""
    return f"{_objectives_key_prefix(project)}:children:{parent_id}"


def _open_set_key(project: str | None) -> str:
    """Redis SET of IDs currently in state='open'."""
    return f"{_objectives_key_prefix(project)}:open"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_objective(obj: Objective, ttl_seconds: int | None = None) -> None:
    """HSET the objective hash; index parent + open-set membership.

    Idempotent: calling again with an updated Objective overwrites fields.
    TTL, if given, is applied to the hash key only (not the index sets, which
    are long-lived).
    """
    try:
        r = _get_redis()
        key = _obj_key(obj.id, obj.project)
        r.hset(key, mapping=obj.to_redis_hash())
        if ttl_seconds is not None and ttl_seconds > 0:
            r.expire(key, ttl_seconds)

        # Parent children index
        if obj.parent_id:
            r.sadd(_children_set_key(obj.parent_id, obj.project), obj.id)

        # Open-set index — only IDs in state 'open'
        open_key = _open_set_key(obj.project)
        if obj.state == "open":
            r.sadd(open_key, obj.id)
        else:
            # If state changed away from open, remove from open set
            r.srem(open_key, obj.id)
    except Exception:
        logger.warning("Failed to write objective %s", obj.id, exc_info=True)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_objective(obj_id: str, project: str | None = None) -> Optional[Objective]:
    """Return the Objective for obj_id, or None if missing / Redis unreachable."""
    try:
        r = _get_redis()
        data = r.hgetall(_obj_key(obj_id, project))
        if not data:
            return None
        return Objective.from_redis_hash(data)
    except Exception:
        logger.warning("Failed to read objective %s", obj_id, exc_info=True)
        return None


def read_children(
    parent_id: str, project: str | None = None
) -> list[Objective]:
    """Return all direct children of parent_id (batch HGETALL)."""
    try:
        r = _get_redis()
        child_ids = r.smembers(_children_set_key(parent_id, project))
        children: list[Objective] = []
        for cid in child_ids:
            try:
                data = r.hgetall(_obj_key(cid, project))
                if data:
                    children.append(Objective.from_redis_hash(data))
            except Exception:
                logger.debug("Skip malformed child %s of %s", cid, parent_id)
        return children
    except Exception:
        logger.warning("Failed to read children of %s", parent_id, exc_info=True)
        return []


def read_tree(
    root_id: str, project: str | None = None, max_depth: int = 10
) -> dict:
    """Return a nested dict ``{"objective": Objective, "children": [...]}``.

    Depth-limited to guard against cycles or unexpectedly deep trees.
    Children whose hashes have been deleted (or were never written) are skipped.
    """
    def _recurse(obj_id: str, depth: int) -> dict | None:
        if depth > max_depth:
            return None
        obj = read_objective(obj_id, project)
        if obj is None:
            return None
        kids = read_children(obj_id, project) if depth < max_depth else []
        child_nodes = []
        for kid in kids:
            node = _recurse(kid.id, depth + 1)
            if node is not None:
                child_nodes.append(node)
        return {"objective": obj, "children": child_nodes}

    result = _recurse(root_id, 1)
    if result is None:
        return {}
    return result


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def query_open(
    project: str | None = None,
    tag: str | None = None,
    min_bounty: int | None = None,
    subtree_root: str | None = None,
    capability: str | None = None,
) -> list[Objective]:
    """Return all open objectives, optionally filtered.

    Filters applied in Python (avoids RediSearch dependency). Performance is
    acceptable while the open set fits in memory; v0.8.2 adds a sweeper
    that culls stale entries.

    If ``subtree_root`` is given, only objectives that are descendants of that
    root (or the root itself) are returned.
    """
    try:
        r = _get_redis()
        open_ids = r.smembers(_open_set_key(project))
    except Exception:
        logger.warning("Failed to read open set for project=%s", project, exc_info=True)
        return []

    # Build descendant set for subtree filtering (lazy, only if needed)
    descendant_ids: set[str] | None = None
    if subtree_root is not None:
        descendant_ids = _collect_descendants(subtree_root, project)
        descendant_ids.add(subtree_root)

    results: list[Objective] = []
    for oid in open_ids:
        try:
            data = r.hgetall(_obj_key(oid, project))
            if not data:
                continue
            obj = Objective.from_redis_hash(data)
        except Exception:
            logger.debug("Skip malformed open objective %s", oid)
            continue

        # Double-check: the set might be stale if a concurrent transition missed srem
        if obj.state != "open":
            continue

        if tag is not None and tag not in obj.tags:
            continue
        if min_bounty is not None and obj.bounty_mind < min_bounty:
            continue
        if capability is not None and capability not in obj.capabilities_required:
            continue
        if descendant_ids is not None and obj.id not in descendant_ids:
            continue

        results.append(obj)

    return results


def _collect_descendants(root_id: str, project: str | None) -> set[str]:
    """BFS to collect all descendant IDs (not including root itself)."""
    visited: set[str] = set()
    frontier = [root_id]
    while frontier:
        current = frontier.pop()
        children = read_children(current, project)
        for child in children:
            if child.id not in visited:
                visited.add(child.id)
                frontier.append(child.id)
    return visited


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


def claim_objective(
    obj_id: str, agent: str, project: str | None = None
) -> bool:
    """Atomically claim an open objective for ``agent``.

    Uses WATCH + MULTI/EXEC for optimistic concurrency: if the state changes
    between WATCH and EXEC, the transaction aborts and we return False.

    Returns True on success, False if already claimed or Redis fails.
    """
    try:
        import redis as _redis  # type: ignore[import-untyped]

        r = _get_redis()
        key = _obj_key(obj_id, project)
        open_key = _open_set_key(project)

        with r.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    state = pipe.hget(key, "state")
                    if state != "open":
                        pipe.reset()
                        return False

                    now = _now_iso()
                    pipe.multi()
                    pipe.hset(
                        key,
                        mapping={
                            "state": "claimed",
                            "holder_agent": agent,
                            "holder_heartbeat_at": now,
                            "updated_at": now,
                        },
                    )
                    pipe.srem(open_key, obj_id)
                    pipe.execute()
                    return True
                except _redis.WatchError:
                    # Another writer changed the key; retry once
                    continue
    except Exception:
        logger.warning("Failed to claim objective %s", obj_id, exc_info=True)
        return False


def heartbeat_objective(obj_id: str, project: str | None = None) -> bool:
    """Bump holder_heartbeat_at and updated_at. Returns False if obj missing."""
    try:
        r = _get_redis()
        key = _obj_key(obj_id, project)
        if not r.exists(key):
            return False
        now = _now_iso()
        r.hset(
            key,
            mapping={
                "holder_heartbeat_at": now,
                "updated_at": now,
            },
        )
        return True
    except Exception:
        logger.warning("Failed to heartbeat objective %s", obj_id, exc_info=True)
        return False


def release_objective(obj_id: str, project: str | None = None) -> bool:
    """Transition state back to 'open'; clear holder fields; re-add to open set.

    Uses HDEL to remove ``holder_agent`` and ``holder_heartbeat_at`` rather than
    writing the string ``"null"``.  ``from_redis_hash`` treats a missing field
    as ``None``, so both old (string "null") and new (absent) forms round-trip
    to ``holder_agent=None``.
    """
    try:
        r = _get_redis()
        key = _obj_key(obj_id, project)
        if not r.exists(key):
            return False
        now = _now_iso()
        r.hset(
            key,
            mapping={
                "state": "open",
                "updated_at": now,
            },
        )
        # Remove the holder fields entirely instead of writing the literal "null".
        r.hdel(key, "holder_agent", "holder_heartbeat_at")
        r.sadd(_open_set_key(project), obj_id)
        return True
    except Exception:
        logger.warning("Failed to release objective %s", obj_id, exc_info=True)
        return False


def complete_objective(
    obj_id: str,
    artifact_url: str,
    notes: str = "",
    project: str | None = None,
) -> bool:
    """Transition state to 'shipped' and attach the completion artifact.

    Does NOT pay out — that is the completion gate's job (Step 9).
    Does NOT remove the holder; they retain credit until paid.
    """
    try:
        r = _get_redis()
        key = _obj_key(obj_id, project)
        if not r.exists(key):
            return False
        now = _now_iso()
        r.hset(
            key,
            mapping={
                "state": "shipped",
                "completion_artifact_url": artifact_url,
                "completion_notes": notes,
                "updated_at": now,
            },
        )
        return True
    except Exception:
        logger.warning("Failed to complete objective %s", obj_id, exc_info=True)
        return False


def ack_completion(
    obj_id: str, acker: str, project: str | None = None
) -> bool:
    """Append ``acker`` to the acks list (deduplicated).

    Does NOT transition state to 'paid' — that is the completion gate (Step 9).
    Returns False if the objective does not exist.
    """
    try:
        r = _get_redis()
        key = _obj_key(obj_id, project)
        if not r.exists(key):
            return False

        raw = r.hget(key, "acks")
        import json

        existing: list[str] = json.loads(raw) if raw and raw != "null" else []
        if acker not in existing:
            existing.append(acker)
        now = _now_iso()
        r.hset(
            key,
            mapping={
                "acks": json.dumps(existing),
                "updated_at": now,
            },
        )
        return True
    except Exception:
        logger.warning("Failed to ack completion for objective %s", obj_id, exc_info=True)
        return False
