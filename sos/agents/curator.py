"""Curator — standing agent that harvests top-decile winners into memory.

Sprint S4 of v0.8.1 closes the compounding loop:

1. Pulse posts a ``kind:harvest-winners`` objective for each active project
   (see :mod:`sos.services.operations.pulse`).
2. Curator claims those objectives, reads the last 24h of audit-stream
   entries from ``sos:stream:global:objectives``, filters to paid objectives
   with ``outcome_score >= WINNER_THRESHOLD``, and writes one memory entry
   per winner tagged ``role:<role>`` + ``kind:winner``.
3. Agents that opt into :func:`sos.kernel.demo_bank.fetch_winners` then
   retrieve those winners as few-shot examples — RAG only, no fine-tuning.

Curator also handles ``kind:postmortem`` objectives (posted by S6 per paid
root).  For v0.8.1 the postmortem is a stub memory entry summarising the
root's children; S6 can extend the summary shape later without touching
the claim/ack loop.

The agent is deliberately fail-soft at every I/O boundary: audit-stream
unreachable, memory-service 500, single-winner JSON parse error — none of
these can be allowed to wedge a harvest objective in a claimed state.  When
writes fail we still ack so the objective doesn't stick, and we log loudly.

CLI:
    python -m sos.agents.curator

Environment:
- ``SOS_OBJECTIVES_URL`` / ``SOS_OBJECTIVES_TOKEN``
- ``SOS_MEMORY_URL`` / ``SOS_MEMORY_TOKEN``
- ``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_PASSWORD`` (for audit-stream read)
- ``SOS_SYSTEM_TOKEN`` — fallback for either service
- ``CURATOR_POLL_INTERVAL`` — seconds between ``run_once`` ticks (default 300)
- ``CURATOR_PROJECT`` — optional project-scope filter
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional

import httpx

from sos.clients.objectives import AsyncObjectivesClient
from sos.contracts.objective import Objective

logger = logging.getLogger("sos.agents.curator")

# ---------------------------------------------------------------------------
# Module-level knobs — monkeypatch-friendly
# ---------------------------------------------------------------------------

WINNER_THRESHOLD: float = 0.7
"""Minimum ``outcome_score`` for a paid objective to count as a winner."""

AUDIT_LOOKBACK_SECONDS: int = 24 * 60 * 60
"""How far back to scan the audit stream for paid events."""

DEFAULT_POLL_INTERVAL_S: int = 300
AGENT_NAME: str = "curator"


def derive_role(objective: Objective) -> str:
    """Return the role associated with an objective.

    Search order:
    1. First tag starting with ``role:`` (e.g. ``role:social`` -> ``social``).
    2. First entry in ``capabilities_required``.
    3. Fallback ``"general"``.

    A return value of ``"general"`` means the winner bucket isn't
    role-specific; agents can still ``fetch_winners("general")`` to
    access it.
    """
    for tag in objective.tags or []:
        if isinstance(tag, str) and tag.startswith("role:"):
            role = tag.split(":", 1)[1].strip()
            if role:
                return role
    if objective.capabilities_required:
        return objective.capabilities_required[0]
    return "general"


# ---------------------------------------------------------------------------
# Curator
# ---------------------------------------------------------------------------


class CuratorAgent:
    """Standing curator that turns paid winners into retrievable memories."""

    def __init__(
        self,
        *,
        objectives_url: Optional[str] = None,
        objectives_token: Optional[str] = None,
        memory_url: Optional[str] = None,
        memory_token: Optional[str] = None,
        audit_stream: str = "sos:stream:global:objectives",
        project: Optional[str] = None,
        agent_name: str = AGENT_NAME,
        http_timeout_s: float = 5.0,
    ) -> None:
        self._objectives_url = objectives_url
        self._objectives_token = objectives_token
        self._memory_url = memory_url or os.environ.get("SOS_MEMORY_URL") or "http://localhost:6061"
        self._memory_token = (
            memory_token
            or os.environ.get("SOS_MEMORY_TOKEN")
            or os.environ.get("SOS_SYSTEM_TOKEN")
        )
        self._audit_stream = audit_stream
        self._project = project
        self._agent_name = agent_name
        self._http_timeout_s = http_timeout_s

    # ------------------------------------------------------------------
    # Public loop entry point
    # ------------------------------------------------------------------

    async def run_once(self) -> int:
        """Claim + process all open harvest objectives. Returns winners written."""
        client = AsyncObjectivesClient(
            base_url=self._objectives_url,
            token=self._objectives_token,
        )

        try:
            harvest = await client.query(tag="kind:harvest-winners", project=self._project)
        except Exception as exc:
            logger.warning("curator: query harvest objectives failed: %s", exc)
            harvest = []

        try:
            postmortem = await client.query(tag="kind:postmortem", project=self._project)
        except Exception as exc:
            logger.warning("curator: query postmortem objectives failed: %s", exc)
            postmortem = []

        total_written = 0

        for obj in harvest:
            if obj.state != "open":
                continue
            try:
                written = await self._process_harvest(client, obj)
                total_written += written
            except Exception as exc:
                logger.exception("curator: harvest %s raised: %s", obj.id, exc)

        for obj in postmortem:
            if obj.state != "open":
                continue
            try:
                await self._process_postmortem(client, obj)
            except Exception as exc:
                logger.exception("curator: postmortem %s raised: %s", obj.id, exc)

        return total_written

    # ------------------------------------------------------------------
    # Harvest pipeline
    # ------------------------------------------------------------------

    async def _process_harvest(
        self,
        client: AsyncObjectivesClient,
        objective: Objective,
    ) -> int:
        """Claim one harvest objective → write winners → complete + ack."""
        obj_project = objective.project or self._project

        try:
            await client.claim(
                objective.id, agent=self._agent_name, project=obj_project
            )
        except Exception as exc:
            logger.warning("curator: claim %s failed: %s", objective.id, exc)
            return 0

        try:
            await client.heartbeat(objective.id, project=obj_project)
        except Exception as exc:
            logger.debug("curator: heartbeat %s failed: %s", objective.id, exc)

        # Pull the last-24h paid events off the audit stream.
        paid_events = await self._read_recent_paid_events()
        winners = [ev for ev in paid_events if self._is_winner(ev)]

        written = 0
        for event in winners:
            try:
                ok = await self._write_winner_memory(event, harvest_project=obj_project)
                if ok:
                    written += 1
            except Exception as exc:
                logger.warning(
                    "curator: memory write failed for %s: %s",
                    event.get("id", "<unknown>"),
                    exc,
                )

        artifact_url = (
            f"memory://winners?harvest={objective.id}&count={written}"
        )
        try:
            await client.complete(
                objective.id,
                artifact_url=artifact_url,
                notes=f"harvested {written} winners",
                project=obj_project,
            )
        except Exception as exc:
            logger.warning("curator: complete %s failed: %s", objective.id, exc)

        try:
            await client.ack(
                objective.id, acker=self._agent_name, project=obj_project
            )
        except Exception as exc:
            logger.warning("curator: ack %s failed: %s", objective.id, exc)

        logger.info(
            "curator: harvested %d winners from %s (project=%s)",
            written,
            objective.id,
            obj_project,
        )
        return written

    async def _process_postmortem(
        self,
        client: AsyncObjectivesClient,
        objective: Objective,
    ) -> None:
        """Stub postmortem: write a summary memory + ack.

        S6 will extend the summary; for v0.8.1 we just make sure the
        objective doesn't wedge.  The root-id is encoded in the objective
        id (``postmortem-<root_id>``) or in tags.
        """
        obj_project = objective.project or self._project

        try:
            await client.claim(
                objective.id, agent=self._agent_name, project=obj_project
            )
        except Exception as exc:
            logger.warning("curator: claim postmortem %s failed: %s", objective.id, exc)
            return

        root_id = ""
        if objective.id.startswith("postmortem-"):
            root_id = objective.id[len("postmortem-"):]
        else:
            for tag in objective.tags or []:
                if isinstance(tag, str) and tag.startswith("root:"):
                    root_id = tag.split(":", 1)[1]
                    break

        summary = (
            f"postmortem for {root_id or objective.id}: "
            f"bounty={objective.bounty_mind} $MIND, "
            f"project={obj_project}, title={objective.title!r}"
        )

        try:
            await self._post_memory(
                content=summary,
                metadata={
                    "kind": "postmortem",
                    "objective_id": objective.id,
                    "root_id": root_id,
                    "project": obj_project,
                },
                tags=["kind:postmortem", f"project:{obj_project or 'default'}"],
            )
        except Exception as exc:
            logger.warning("curator: postmortem memory write failed: %s", exc)

        try:
            await client.complete(
                objective.id,
                artifact_url=f"memory://postmortem/{objective.id}",
                notes=summary,
                project=obj_project,
            )
        except Exception as exc:
            logger.warning(
                "curator: complete postmortem %s failed: %s", objective.id, exc
            )

        try:
            await client.ack(
                objective.id, acker=self._agent_name, project=obj_project
            )
        except Exception as exc:
            logger.warning("curator: ack postmortem %s failed: %s", objective.id, exc)

    # ------------------------------------------------------------------
    # Audit-stream read
    # ------------------------------------------------------------------

    async def _read_recent_paid_events(self) -> list[dict[str, Any]]:
        """Return paid-state audit events from the last ``AUDIT_LOOKBACK_SECONDS``.

        Each element is the decoded ``payload`` dict (plus ``id`` / ``outcome_score``
        lifted up so the rest of the curator can treat it flat).  Fail-soft
        returns ``[]`` on any Redis trouble.
        """
        try:
            import redis as _redis  # local import — keep module importable w/o redis
        except Exception as exc:
            logger.warning("curator: redis import failed: %s", exc)
            return []

        try:
            host = os.environ.get("REDIS_HOST", "127.0.0.1")
            port = int(os.environ.get("REDIS_PORT", "6379"))
            pw = os.environ.get("REDIS_PASSWORD", "") or None
            r = _redis.Redis(host=host, port=port, password=pw, decode_responses=True)
            since_ms = int((time.time() - AUDIT_LOOKBACK_SECONDS) * 1000)
            raw = r.xrange(self._audit_stream, min=f"{since_ms}-0", max="+")
        except Exception as exc:
            logger.warning("curator: audit-stream read failed: %s", exc)
            return []

        events: list[dict[str, Any]] = []
        for _entry_id, fields in raw:
            payload_raw = fields.get("payload") if isinstance(fields, dict) else None
            if not payload_raw:
                continue
            try:
                payload = json.loads(payload_raw)
            except Exception:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
                # Some writers double-nest (see app.py paid transition emit).
                payload = payload["payload"]
            if not isinstance(payload, dict):
                continue
            if payload.get("new_state") != "paid":
                continue
            events.append(payload)
        return events

    def _is_winner(self, event: dict[str, Any]) -> bool:
        """True iff the audit event has a usable outcome_score ≥ threshold."""
        score = event.get("outcome_score")
        if score is None:
            return False
        try:
            return float(score) >= WINNER_THRESHOLD
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Memory writes
    # ------------------------------------------------------------------

    async def _write_winner_memory(
        self,
        event: dict[str, Any],
        *,
        harvest_project: Optional[str],
    ) -> bool:
        """Fetch the full objective, derive role, post a winner memory."""
        obj_id = event.get("id")
        if not obj_id:
            return False

        event_project = event.get("project") or harvest_project

        # Best-effort read the full objective so we have title + artifact.
        client = AsyncObjectivesClient(
            base_url=self._objectives_url,
            token=self._objectives_token,
        )
        try:
            obj = await client.get(obj_id, project=event_project)
        except Exception as exc:
            logger.warning("curator: fetch objective %s failed: %s", obj_id, exc)
            obj = None

        if obj is None:
            role = "general"
            prompt = event.get("title") or obj_id
            artifact = event.get("artifact_url") or ""
            project = event_project
            score = event.get("outcome_score")
        else:
            role = derive_role(obj)
            prompt = obj.description or obj.title
            artifact = obj.completion_artifact_url or ""
            project = obj.project or event_project
            score = (
                obj.outcome_score
                if obj.outcome_score is not None
                else event.get("outcome_score")
            )

        tags = [f"role:{role}", "kind:winner"]
        if project:
            tags.append(f"project:{project}")

        metadata: dict[str, Any] = {
            "outcome_score": score,
            "objective_id": obj_id,
            "role": role,
            "project": project,
            "kind": "winner",
        }

        payload = {
            "prompt": prompt,
            "artifact": artifact,
            "role": role,
            "kind": "winner",
            "tag": f"role:{role}",
            "metadata": metadata,
        }

        return await self._post_memory(
            content=artifact or prompt,
            metadata=metadata,
            tags=tags,
            extra=payload,
        )

    async def _post_memory(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        tags: list[str],
        extra: Optional[dict[str, Any]] = None,
    ) -> bool:
        """POST /memories on the memory service.  Fail-soft.

        The memory service is assumed to expose ``POST /memories`` with a body
        of ``{content, metadata, tags, ...}``.  Any non-2xx or network error
        is logged and ``False`` is returned — the caller decides whether that
        should skip an ack (it shouldn't; we ack regardless to prevent wedges).
        """
        url = f"{self._memory_url.rstrip('/')}/memories"
        headers: dict[str, str] = {}
        if self._memory_token:
            headers["Authorization"] = f"Bearer {self._memory_token}"

        body: dict[str, Any] = {
            "content": content,
            "metadata": metadata,
            "tags": tags,
        }
        if extra:
            body.update(extra)

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_s) as http:
                resp = await http.post(url, json=body, headers=headers)
        except Exception as exc:
            logger.warning("curator: memory POST raised: %s", exc)
            return False

        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "curator: memory POST returned %s (%s)",
            resp.status_code,
            resp.text[:200],
        )
        return False


# ---------------------------------------------------------------------------
# CLI — standing loop
# ---------------------------------------------------------------------------


async def _main_loop() -> None:
    poll_s = int(os.environ.get("CURATOR_POLL_INTERVAL", DEFAULT_POLL_INTERVAL_S))
    project = os.environ.get("CURATOR_PROJECT") or None

    agent = CuratorAgent(project=project)
    logger.info("curator: online — project=%s, poll=%ss", project, poll_s)

    while True:
        try:
            written = await agent.run_once()
            logger.info("curator: tick complete — wrote %d winners", written)
        except Exception as exc:
            # run_once is already fail-soft internally, but belt-and-braces.
            logger.exception("curator: run_once raised unexpectedly: %s", exc)

        await asyncio.sleep(poll_s)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [CURATOR] %(message)s",
    )
    try:
        asyncio.run(_main_loop())
    except KeyboardInterrupt:
        logger.info("curator: stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
