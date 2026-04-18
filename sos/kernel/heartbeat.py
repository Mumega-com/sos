"""AgentCard heartbeat helper — v0.7.3.

Every SOS process that wants to appear in ``GET /agents/cards`` calls
:func:`emit_card` on boot and on a short cadence (default 60s is a
reasonable floor given the 300s registry TTL).

The helper intentionally:

- Is fail-soft. The registry may be unreachable, the token may be
  wrong, Redis may be down — none of those should crash a working
  agent. We log and move on.
- Lives in ``sos.kernel`` because every agent substrate (tmux, CLI,
  service, Hermes, Codex) needs it; ``sos.clients.registry`` is
  AgentIdentity-focused and would grow an awkward second surface if
  we wired POST /agents/cards there.
- Uses a thin ``httpx`` POST rather than the full RegistryClient to
  avoid dragging in AgentIdentity deserialization for a write path.

Environment:
- ``SOS_REGISTRY_URL`` — base URL, default ``http://localhost:6067``.
- ``SOS_REGISTRY_TOKEN`` or ``SOS_SYSTEM_TOKEN`` — Bearer token.

If the token is absent the helper returns ``False`` without making a
network call — there is no registry in which to register.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from sos.contracts.agent_card import AgentCard

logger = logging.getLogger("sos.kernel.heartbeat")


DEFAULT_REGISTRY_URL = "http://localhost:6067"
DEFAULT_TIMEOUT_S = 5.0


def _resolve_registry_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get("SOS_REGISTRY_URL") or DEFAULT_REGISTRY_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    if token is not None:
        return token
    return os.environ.get("SOS_REGISTRY_TOKEN") or os.environ.get("SOS_SYSTEM_TOKEN")


def emit_card(
    card: AgentCard,
    *,
    project: Optional[str] = None,
    ttl_seconds: int = 300,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bool:
    """POST ``card`` to ``/agents/cards`` on the registry. Fail-soft.

    Returns ``True`` on 2xx, ``False`` on any other outcome. Never raises.

    Args:
        card: The AgentCard to upsert. ``card.last_seen`` should already
            be set to now by the caller — this helper does not touch the
            card contents.
        project: Optional override for the URL ``?project=`` query arg.
            If ``None``, falls back to ``card.project``.
        ttl_seconds: How long Redis should retain the card before
            expiring it. Pair with caller's heartbeat cadence so the
            TTL is ~3x the emit interval.
        base_url: Override ``SOS_REGISTRY_URL``.
        token: Override ``SOS_REGISTRY_TOKEN`` / ``SOS_SYSTEM_TOKEN``.
        timeout_s: Per-request timeout. Keep short — a slow registry
            must not stall an agent's heartbeat loop.
    """
    resolved_token = _resolve_token(token)
    if resolved_token is None:
        logger.debug("emit_card: no token available, skipping")
        return False

    effective_project = project if project is not None else card.project
    url = f"{_resolve_registry_url(base_url)}/agents/cards"
    params: dict[str, Any] = {"ttl_seconds": ttl_seconds}
    if effective_project:
        params["project"] = effective_project

    headers = {"Authorization": f"Bearer {resolved_token}"}

    try:
        resp = httpx.post(
            url,
            json=card.model_dump(mode="json"),
            params=params,
            headers=headers,
            timeout=timeout_s,
        )
    except Exception as exc:
        logger.debug("emit_card: POST raised %s", exc)
        return False

    if 200 <= resp.status_code < 300:
        return True

    logger.debug(
        "emit_card: registry returned %s (%s)",
        resp.status_code,
        resp.text[:200],
    )
    return False
