"""Fail-soft objective lifecycle helpers — v0.8.0.

Lets any agent claim/heartbeat/release an objective over HTTP without
hard-failing when the objectives service is unreachable.

The helpers intentionally:

- Are fail-soft.  The service may be unreachable, the token may be wrong —
  none of those should crash a working agent.  We log a warning and move on.
- Live in ``sos.kernel`` so every agent substrate can use them without
  pulling in ``sos.services.*`` (which would violate the import linter).
- Accept a string ``objective_id`` only — no import of ``sos.contracts.objective``.

Environment:
- ``SOS_OBJECTIVES_URL`` — base URL, default ``http://localhost:6068``.
- ``SOS_OBJECTIVES_TOKEN`` or ``SOS_SYSTEM_TOKEN`` — Bearer token.

If the token is absent the helper returns ``False`` without making a
network call — there is no service to call.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("sos.kernel.objectives")

DEFAULT_OBJECTIVES_URL = "http://localhost:6068"
DEFAULT_TIMEOUT_S = 5.0


def _resolve_objectives_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get("SOS_OBJECTIVES_URL") or DEFAULT_OBJECTIVES_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    if token is not None:
        return token
    return os.environ.get("SOS_OBJECTIVES_TOKEN") or os.environ.get("SOS_SYSTEM_TOKEN")


def claim(
    objective_id: str,
    *,
    agent: Optional[str] = None,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bool:
    """POST ``/objectives/{objective_id}/claim``.  Fail-soft.

    Returns ``True`` on 2xx, ``False`` on any other outcome.  Never raises.

    Args:
        objective_id: ID of the objective to claim.
        agent: Optional agent identifier to include in the request body.
        base_url: Override ``SOS_OBJECTIVES_URL``.
        token: Override ``SOS_OBJECTIVES_TOKEN`` / ``SOS_SYSTEM_TOKEN``.
        timeout_s: Per-request timeout.
    """
    resolved_token = _resolve_token(token)
    if resolved_token is None:
        return False

    url = f"{_resolve_objectives_url(base_url)}/objectives/{objective_id}/claim"
    headers = {"Authorization": f"Bearer {resolved_token}"}
    body: dict[str, str] = {}
    if agent is not None:
        body["agent"] = agent

    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=timeout_s)
    except Exception as exc:
        logger.warning("claim: POST raised %s", exc)
        return False

    if 200 <= resp.status_code < 300:
        return True

    logger.warning(
        "claim: objectives service returned %s (%s)",
        resp.status_code,
        resp.text[:200],
    )
    return False


def heartbeat_objective(
    objective_id: str,
    *,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bool:
    """POST ``/objectives/{objective_id}/heartbeat``.  Fail-soft.

    Returns ``True`` on 2xx, ``False`` on any other outcome.  Never raises.

    Args:
        objective_id: ID of the objective to heartbeat.
        base_url: Override ``SOS_OBJECTIVES_URL``.
        token: Override ``SOS_OBJECTIVES_TOKEN`` / ``SOS_SYSTEM_TOKEN``.
        timeout_s: Per-request timeout.
    """
    resolved_token = _resolve_token(token)
    if resolved_token is None:
        return False

    url = f"{_resolve_objectives_url(base_url)}/objectives/{objective_id}/heartbeat"
    headers = {"Authorization": f"Bearer {resolved_token}"}

    try:
        resp = httpx.post(url, json={}, headers=headers, timeout=timeout_s)
    except Exception as exc:
        logger.warning("heartbeat_objective: POST raised %s", exc)
        return False

    if 200 <= resp.status_code < 300:
        return True

    logger.warning(
        "heartbeat_objective: objectives service returned %s (%s)",
        resp.status_code,
        resp.text[:200],
    )
    return False


def release(
    objective_id: str,
    *,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bool:
    """POST ``/objectives/{objective_id}/release``.  Fail-soft.

    Returns ``True`` on 2xx, ``False`` on any other outcome.  Never raises.

    Args:
        objective_id: ID of the objective to release.
        base_url: Override ``SOS_OBJECTIVES_URL``.
        token: Override ``SOS_OBJECTIVES_TOKEN`` / ``SOS_SYSTEM_TOKEN``.
        timeout_s: Per-request timeout.
    """
    resolved_token = _resolve_token(token)
    if resolved_token is None:
        return False

    url = f"{_resolve_objectives_url(base_url)}/objectives/{objective_id}/release"
    headers = {"Authorization": f"Bearer {resolved_token}"}

    try:
        resp = httpx.post(url, json={}, headers=headers, timeout=timeout_s)
    except Exception as exc:
        logger.warning("release: POST raised %s", exc)
        return False

    if 200 <= resp.status_code < 300:
        return True

    logger.warning(
        "release: objectives service returned %s (%s)",
        resp.status_code,
        resp.text[:200],
    )
    return False
