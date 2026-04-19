"""Demo-bank — opt-in RAG retrieval of top-decile shipped artifacts.

v0.8.1 Sprint S4 introduces a compounding-loop primitive: after an objective
is paid with an ``outcome_score`` (see S3), the Curator agent harvests the
top-decile entries and writes them into the memory service tagged
``role:<role>`` + ``kind:winner``.  This module is how agents pull those
winners back out and glue them onto their prompt — *retrieval only, no
fine-tuning*.

Design notes:

- Fail-soft at the network boundary.  If the memory service is unreachable,
  :func:`fetch_winners` returns ``[]`` and the agent runs on its base prompt.
  This module must never be the cause of an agent going down.
- Synchronous :func:`build_few_shot_prompt` is a pure function so callers can
  drop it into any prompt pipeline without worrying about event loops.
- Agents opt in.  No implicit injection — each agent explicitly decides
  whether to call ``fetch_winners`` + ``build_few_shot_prompt`` inside its
  own prompt logic.  TROP chooses per-workflow.

Environment:
- ``SOS_MEMORY_URL`` — base URL, default ``http://localhost:6061``.
- ``SOS_MEMORY_TOKEN`` or ``SOS_SYSTEM_TOKEN`` — bearer for the memory
  service.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("sos.kernel.demo_bank")

DEFAULT_MEMORY_URL = "http://localhost:6061"
DEFAULT_TIMEOUT_S = 5.0


def _resolve_memory_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get("SOS_MEMORY_URL") or DEFAULT_MEMORY_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    if token is not None:
        return token
    return os.environ.get("SOS_MEMORY_TOKEN") or os.environ.get("SOS_SYSTEM_TOKEN")


async def fetch_winners(
    role: str,
    *,
    n: int = 10,
    project: Optional[str] = None,
    memory_base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Return up to ``n`` top-decile winners for ``role``, sorted by score desc.

    Queries the memory service for memories tagged ``role:<role>`` and
    ``kind:winner``.  If ``project`` is given, also filters on
    ``project:<project>``.  Results are ordered by
    ``metadata.outcome_score`` descending so the caller can slice from the top.

    Args:
        role: Agent role (e.g. ``"social"``, ``"content"``).  Matches the
            ``role:<x>`` tag written by the Curator.
        n: Maximum number of winners to return.
        project: Optional project-scope filter (e.g. ``"trop"``).
        memory_base_url: Override ``SOS_MEMORY_URL``.
        token: Override ``SOS_MEMORY_TOKEN`` / ``SOS_SYSTEM_TOKEN``.
        timeout_s: Per-request timeout.

    Returns:
        A list of memory entries — each a dict containing at least
        ``prompt``, ``artifact``, ``outcome_score``, and ``objective_id``
        (whatever the memory service returns is preserved).  Returns ``[]``
        on any I/O or auth failure.
    """
    resolved_token = _resolve_token(token)
    url = f"{_resolve_memory_url(memory_base_url)}/search"

    tags = [f"role:{role}", "kind:winner"]
    if project is not None:
        tags.append(f"project:{project}")

    headers: dict[str, str] = {}
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"

    params = {
        "tags": tags,
        "limit": n,
        "order_by": "metadata.outcome_score",
        "order_dir": "desc",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url, params=params, headers=headers)
    except Exception as exc:
        logger.warning("fetch_winners: memory service unreachable: %s", exc)
        return []

    if resp.status_code < 200 or resp.status_code >= 300:
        logger.warning(
            "fetch_winners: memory returned %s (%s)",
            resp.status_code,
            resp.text[:200],
        )
        return []

    try:
        body = resp.json()
    except Exception as exc:
        logger.warning("fetch_winners: could not parse memory response: %s", exc)
        return []

    results: list[dict[str, Any]]
    if isinstance(body, dict):
        results = list(body.get("results", []))
    elif isinstance(body, list):
        results = list(body)
    else:
        return []

    # Defensive re-sort client-side in case the memory service didn't honour
    # the order_by param.  Items without a score sink to the bottom.
    def _score(entry: dict[str, Any]) -> float:
        metadata = entry.get("metadata") or {}
        score = metadata.get("outcome_score")
        if score is None:
            score = entry.get("outcome_score")
        try:
            return float(score) if score is not None else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    results.sort(key=_score, reverse=True)
    return results[:n]


def build_few_shot_prompt(
    base_prompt: str,
    winners: list[dict[str, Any]],
    *,
    max_chars: int = 8000,
) -> str:
    """Append an ``Examples of past high-quality outputs`` section to a prompt.

    Pure function — no I/O.  Trims the assembled prompt to ``max_chars`` so
    that attaching a big winner list can never blow an agent's context budget.

    Each winner dict should at least contain a human-readable ``artifact``
    (required) and optionally ``prompt``, ``outcome_score``, ``objective_id``.
    Missing keys are skipped rather than erroring out — this is a
    best-effort RAG shim.

    Args:
        base_prompt: The agent's own prompt.  Returned as-is if ``winners``
            is empty.
        winners: Output of :func:`fetch_winners` (or a hand-crafted list).
        max_chars: Hard cap on the returned string length.

    Returns:
        The combined prompt, truncated at ``max_chars`` if necessary.
    """
    if not winners:
        return base_prompt[:max_chars]

    lines: list[str] = [
        base_prompt.rstrip(),
        "",
        "Examples of past high-quality outputs:",
    ]

    for idx, winner in enumerate(winners, start=1):
        artifact = winner.get("artifact")
        if not artifact:
            continue
        prompt_text = winner.get("prompt", "")
        score = winner.get("outcome_score")
        if score is None:
            metadata = winner.get("metadata") or {}
            score = metadata.get("outcome_score")

        header = f"Example {idx}"
        if score is not None:
            try:
                header = f"{header} (score={float(score):.2f})"
            except (TypeError, ValueError):
                pass

        lines.append("")
        lines.append(header)
        if prompt_text:
            lines.append(f"  Prompt: {prompt_text}")
        lines.append(f"  Output: {artifact}")

    combined = "\n".join(lines)
    if len(combined) <= max_chars:
        return combined

    # Trim at a newline boundary when we can so we don't cut mid-word.
    truncated = combined[:max_chars]
    cut = truncated.rfind("\n")
    if cut > max_chars // 2:
        truncated = truncated[:cut]
    return truncated


__all__ = ["fetch_winners", "build_few_shot_prompt"]
