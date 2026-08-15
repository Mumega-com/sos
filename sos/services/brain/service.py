"""BrainService — async bus consumer for scoring and dispatch.

v0.9.1 W6 — migrates from XREAD + Redis checkpoint to XREADGROUP + XACK
at-least-once. Semantics:

* Success (handler returns normally, including graceful ``_malformed`` /
  ``_unknown`` paths inside ``_handle_event``) → ``XACK``.
* Exception inside ``_handle_event`` → leave unacked. The bus retry
  worker (:class:`sos.services.bus.retry.RetryWorker`) reclaims the
  entry after backoff; terminal failures go to DLQ after
  ``max_retry`` tries. No silent drops.
* Duplicate envelope ``message_id`` (retry re-XADDs produce a new
  stream entry with the same logical id) → XACK + skip. The
  ``_LRUSet`` absorbs that envelope-level dedup so handlers fire
  exactly once per logical event.

BrainState, snapshot, OTEL trace_id, and task.scored / task.routed
emission are unchanged — only the consumption path changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import redis.asyncio as aioredis

from sos.clients.registry import AsyncRegistryClient
from sos.contracts.brain_snapshot import BrainSnapshot
from sos.contracts.brain_snapshot import RoutingDecision as SnapshotRoute
from sos.contracts.messages import (
    BusMessage,
    TaskRoutedMessage,
    TaskRoutedPayload,
    TaskScoredMessage,
    TaskScoredPayload,
)
from sos.kernel.trace_context import get_current_trace_id, use_trace_id
from sos.kernel.telemetry import init_tracing, span_under_current_trace
from sos.services.brain.matrix import agent_load, select_agent  # noqa: F401
from sos.services.brain.scoring import score_task
from sos.services.brain.source_reader import (
    MissingProjectIdError,
    extract_project_from_stream,
    is_active,
)
from sos.services.brain.state import BrainState, RoutingDecision, RoutingOutcome

if TYPE_CHECKING:
    from sos.services.bus.redis_bus import RedisBusService

logger = logging.getLogger("sos.brain")

# ---------------------------------------------------------------------------
# Registry HTTP client (P0-09 — Wave 5)
# ---------------------------------------------------------------------------
# The Brain no longer imports the registry service module directly. It reaches
# the canonical agent registry over HTTP via the Registry service (port 6067).
from sos.kernel.settings import get_settings as _get_settings  # noqa: E402

_brain_settings = _get_settings()
_registry_client = AsyncRegistryClient(
    base_url=_brain_settings.services.registry,
    token=(
        _brain_settings.auth.registry_token.get_secret_value()
        if _brain_settings.auth.registry_token
        else _brain_settings.auth.system_token_str or None
    ),
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Redis key where the latest BrainSnapshot JSON is persisted (TTL 30s).
# Dashboard reads this on GET /sos/brain.
_BRAIN_SNAPSHOT_KEY = "sos:state:brain:snapshot"
_BRAIN_SNAPSHOT_TTL_SEC = 30

# Stream the Brain emits task.scored events on
_BRAIN_EMIT_STREAM = "sos:stream:global:squad:brain"

# S061 Track H — adaptive routing outcome stream consumed by Dreamer.
_BRAIN_OUTCOME_STREAM = "sos:stream:global:brain:outcomes"

# S062 Track A — human escalation stream when FSD cannot route a task.
_HUMAN_ESCALATION_STREAM = "sos:stream:project:sos:agent:river"

# XREADGROUP blocking timeout in milliseconds
_BLOCK_MS = 1000

# How many seen message_ids to keep for idempotency (LRU cap)
_LRU_CAPACITY = 10_000

# Streams the Brain cares about
DEFAULT_STREAM_PATTERNS: list[str] = [
    "sos:stream:global:squad:*",
    "sos:stream:global:registry",
    "sos:stream:global:agent:*",
]

# S064 Track A — Experience Ledger stream (Mirror → Brain indexer)
_EXPERIENCE_STREAM = "experience:stream"

# Redis key pattern for experience index sorted sets
# ZADD experience:idx:{action_type}:{target_industry} {metric_value} {engram_id}
_EXPERIENCE_IDX_PREFIX = "experience:idx"

# Message types this service actively handles
_BRAIN_HANDLED_TYPES: frozenset[str] = frozenset(
    {
        "task.created",
        "task.completed",
        "task.failed",
        "task.routed",
        "agent_joined",
        "experience.stored",
    }
)


# ---------------------------------------------------------------------------
# Small LRU for idempotency
# ---------------------------------------------------------------------------


class _LRUSet:
    """Bounded set that evicts the oldest entry when at capacity."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._store: OrderedDict[str, None] = OrderedDict()

    def __contains__(self, item: str) -> bool:
        return item in self._store

    def add(self, item: str) -> None:
        if item in self._store:
            self._store.move_to_end(item)
            return
        self._store[item] = None
        if len(self._store) > self._capacity:
            self._store.popitem(last=False)


# ---------------------------------------------------------------------------
# Redis client factory
# ---------------------------------------------------------------------------


def _build_redis_url() -> str:
    from sos.kernel.settings import get_settings as _get_settings

    return _get_settings().redis.build_url()


# ---------------------------------------------------------------------------
# BrainService
# ---------------------------------------------------------------------------


class BrainService:
    """Event-driven scoring and dispatch for SOS work queue.

    Subscribes to sos:stream:global:squad:*, sos:stream:global:registry,
    and sos:stream:global:agent:* via Redis XREADGROUP. Uses at-least-once
    delivery: entries are XACK'd only after a successful handler call.
    Handler exceptions leave entries unacked for the retry worker to reclaim.
    Envelope-level dedup via _LRUSet (retry re-XADDs produce new stream
    entries sharing the same envelope message_id).
    """

    def __init__(
        self,
        redis_url: str | None = None,
        stream_patterns: list[str] | None = None,
        consumer_name: str = "brain",
        group_name: str = "brain",
        redis_client: Optional[aioredis.Redis] = None,  # injectable for tests
        bus_service: Optional["RedisBusService"] = None,
    ) -> None:
        self._redis_url = redis_url or _build_redis_url()
        self._stream_patterns = stream_patterns or DEFAULT_STREAM_PATTERNS
        self._consumer_name = consumer_name
        self._group_name = group_name

        # Injected redis client takes precedence (used in tests via fakeredis)
        self._redis: Optional[aioredis.Redis] = redis_client
        self._bus_service = bus_service

        self._seen_ids: _LRUSet = _LRUSet(_LRU_CAPACITY)
        # Streams we've already ensured a consumer group on.
        self._groups_registered: set[str] = set()
        self._running = False
        self._stop_event: asyncio.Event = asyncio.Event()

        # Observable state
        self.state: BrainState = BrainState()

        # Snapshot — when this BrainService instance booted (ISO-8601 UTC).
        self._service_started_at: str = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop. Runs until .stop() is called."""
        self._running = True
        self._stop_event.clear()
        logger.info(
            "BrainService starting (consumer=%s, group=%s, patterns=%s)",
            self._consumer_name,
            self._group_name,
            self._stream_patterns,
        )

        # OTEL: idempotent, so safe even if init_tracing already ran.
        init_tracing("brain")

        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unhandled error in BrainService main loop; continuing")
                await asyncio.sleep(1)

        logger.info("BrainService stopped")
        self._running = False

    def stop(self) -> None:
        """Signal the loop to shut down cleanly."""
        logger.info("BrainService.stop() called")
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Internal loop tick
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        streams = await self._discover_streams()
        if not streams:
            await self._persist_snapshot()
            await asyncio.sleep(1.0)
            return

        for stream in streams:
            if stream not in self._groups_registered:
                await self._ensure_group(stream)
                self._groups_registered.add(stream)

        read_spec: dict[str, str] = {s: ">" for s in streams}

        try:
            assert self._redis is not None
            results = await self._redis.xreadgroup(
                groupname=self._group_name,
                consumername=self._consumer_name,
                streams=read_spec,
                count=50,
                block=_BLOCK_MS,
            )
        except Exception:
            logger.exception("XREADGROUP error; sleeping 5s")
            await asyncio.sleep(5)
            return

        if not results:
            await self._persist_snapshot()
            return

        for stream_raw, entries in results:
            # Redis may return bytes or str depending on decode_responses
            stream = stream_raw if isinstance(stream_raw, str) else stream_raw.decode()
            for entry_id_raw, fields_raw in entries:
                entry_id = entry_id_raw if isinstance(entry_id_raw, str) else entry_id_raw.decode()
                # Normalise field keys/values
                fields: dict[str, str] = {
                    (k if isinstance(k, str) else k.decode()): (
                        v if isinstance(v, str) else v.decode()
                    )
                    for k, v in (fields_raw.items() if hasattr(fields_raw, "items") else fields_raw)
                }

                # Idempotency — check message_id in the envelope
                msg_id = fields.get("message_id", "") or entry_id
                if msg_id in self._seen_ids:
                    logger.debug("Duplicate message_id=%s, ack+skip", msg_id)
                    await self._emit_duplicate_suppressed(stream, entry_id, fields)
                    assert self._redis is not None
                    await self._redis.xack(stream, self._group_name, entry_id)
                    continue

                # Extract inbound trace_id (or mint one) and make it the active
                # context for the handler — downstream audits and emits pick
                # it up without needing it threaded through every signature.
                trace_id = fields.get("trace_id") or BusMessage.new_trace_id()
                try:
                    with (
                        use_trace_id(trace_id),
                        span_under_current_trace(
                            f"bus.handle.{stream}",
                            tracer_name="sos.brain",
                            attributes={
                                "sos.stream": stream,
                                "sos.entry_id": entry_id,
                            },
                        ),
                    ):
                        await self._handle_event(stream, entry_id, fields)
                except MissingProjectIdError:
                    # BLOCK-3 (G64b): ACK + emit + log ERROR + continue.
                    # Retrying won't fix a missing project_id — ACK to prevent
                    # infinite re-delivery. Emit for observability.
                    self._seen_ids.add(msg_id)
                    assert self._redis is not None
                    await self._redis.xack(stream, self._group_name, entry_id)
                    try:
                        from sos.observability.sprint_telemetry import emit_missing_project_id
                        emit_missing_project_id(msg_id, stream)
                    except Exception:
                        pass  # non-blocking fire-and-forget (WARN-1)
                    logger.error(
                        "Missing project_id on stream=%s entry=%s; ACK'd + emitted",
                        stream,
                        entry_id,
                    )
                    continue
                except Exception:
                    # Leave unacked — retry worker reclaims after backoff.
                    # Do NOT XACK, do NOT mark seen.
                    logger.exception(
                        "Handler failed on stream=%s entry=%s; leaving unacked for retry",
                        stream,
                        entry_id,
                    )
                    continue

                # Handler returned normally (including graceful skip paths).
                self._seen_ids.add(msg_id)
                assert self._redis is not None
                await self._redis.xack(stream, self._group_name, entry_id)

        # Publish the latest observable snapshot so the dashboard can read it.
        await self._persist_snapshot()

    # ------------------------------------------------------------------
    # Stream discovery
    # ------------------------------------------------------------------

    async def _discover_streams(self) -> list[str]:
        """SCAN redis for all streams matching our patterns."""
        found: list[str] = []
        assert self._redis is not None
        for pattern in self._stream_patterns:
            cursor: int = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                for k in keys:
                    key = k if isinstance(k, str) else k.decode()
                    if key not in found:
                        found.append(key)
                if cursor == 0:
                    break
        # S064 Track A — always include the experience stream so BrainService
        # can index engrams even if Mirror hasn't written to it yet this cycle.
        if _EXPERIENCE_STREAM not in found:
            found.append(_EXPERIENCE_STREAM)
        return found

    # ------------------------------------------------------------------
    # Consumer group registration
    # ------------------------------------------------------------------

    async def _ensure_group(self, stream: str) -> None:
        """Idempotently create/register the consumer group on ``stream``.

        Uses ``bus_service.register_consumer_group`` when available so
        the retry worker picks up unacked entries; falls back to a raw
        ``XGROUP CREATE`` with ``MKSTREAM`` (swallowing BUSYGROUP) so
        unit tests with just a fake redis still get XREADGROUP semantics.
        """
        if self._bus_service is not None:
            await self._bus_service.register_consumer_group(stream, self._group_name)
            return
        assert self._redis is not None
        try:
            await self._redis.xgroup_create(
                name=stream, groupname=self._group_name, id="0", mkstream=True
            )
        except Exception as exc:  # redis.exceptions.ResponseError when BUSYGROUP
            if "BUSYGROUP" not in str(exc):
                raise

    # ------------------------------------------------------------------
    # Snapshot (dashboard hand-off)
    # ------------------------------------------------------------------

    def snapshot(self) -> BrainSnapshot:
        """Build a BrainSnapshot from the current BrainState.

        Read-only on ``self.state``. Safe to call at any time; the dashboard
        service reads the serialised form from redis — it never calls this
        method directly (cross-service imports are forbidden).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        recent: list[SnapshotRoute] = [
            SnapshotRoute(
                task_id=rd.task_id,
                agent_name=rd.agent_name,
                score=rd.score,
                routed_at=rd.routed_at,
            )
            for rd in self.state.recent_routing_decisions
        ]
        return BrainSnapshot(
            queue_size=self.state.queue_size(),
            in_flight=sorted(self.state.tasks_in_flight),
            recent_routes=recent,
            events_by_type=dict(self.state.events_by_type),
            events_seen=self.state.events_seen,
            last_update_ts=self.state.last_event_at or now_iso,
            service_started_at=self._service_started_at,
        )

    async def _persist_snapshot(self) -> None:
        """Serialise the snapshot to redis with a 30 s TTL.

        The short TTL is intentional: if BrainService stops ticking, the key
        expires and the dashboard returns 503 — operators can detect a dead
        brain immediately.
        """
        if self._redis is None:
            return
        try:
            payload = self.snapshot().model_dump_json()
            await self._redis.set(_BRAIN_SNAPSHOT_KEY, payload, ex=_BRAIN_SNAPSHOT_TTL_SEC)
        except Exception:
            logger.exception("Failed to persist brain snapshot to redis")

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def _handle_event(self, stream: str, entry_id: str, fields: dict[str, str]) -> None:
        """Parse v1 envelope and dispatch to the appropriate handler stub."""
        # S064 Track A: experience:stream uses 'event' key instead of 'type'
        msg_type = fields.get("type", "") or fields.get("event", "")
        now_iso = datetime.now(timezone.utc).isoformat()

        # Parse payload JSON — tolerate malformed payloads
        raw_payload = fields.get("payload", "{}")
        try:
            payload: dict = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("Malformed payload on stream=%s entry=%s; skipping", stream, entry_id)
            # Update state counters even for malformed messages
            self.state.record_event("_malformed", now_iso)
            return

        msg: dict = {"type": msg_type, "stream": stream, "entry_id": entry_id, **payload}

        if msg_type not in _BRAIN_HANDLED_TYPES:
            logger.debug("Unhandled type=%r on stream=%s; skipping gracefully", msg_type, stream)
            self.state.record_event(msg_type or "_unknown", now_iso)
            return

        # Update state counters
        self.state.record_event(msg_type, now_iso)

        # Dispatch
        if msg_type == "task.created":
            await self._on_task_created(msg)
        elif msg_type == "task.completed":
            await self._on_task_completed(msg)
        elif msg_type == "task.failed":
            await self._on_task_failed(msg)
        elif msg_type == "task.routed":
            await self._on_task_routed(msg)
        elif msg_type == "agent_joined":
            await self._on_agent_joined(msg)
        elif msg_type == "experience.stored":
            await self._on_experience_stored(fields)

    # ------------------------------------------------------------------
    # S061 Track H — slime-mold routing outcome signals
    # ------------------------------------------------------------------

    @staticmethod
    def _project_event_stream(project: str | None) -> str:
        return f"sos:stream:project:{project}:dreamer" if project else "sos:stream:global:dreamer"

    async def _emit_duplicate_suppressed(
        self,
        stream: str,
        entry_id: str,
        fields: dict[str, str],
    ) -> None:
        raw_payload = fields.get("payload", "{}")
        try:
            payload = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        await self._emit_routing_outcome(
            outcome_type="duplicate_suppressed",
            project=str(payload.get("project") or payload.get("project_id") or extract_project_from_stream(stream) or ""),
            task_id=str(payload.get("task_id") or payload.get("id") or fields.get("message_id") or entry_id),
            agent_name="brain",
            status="suppressed",
            reason="duplicate message_id acked without handler replay",
            extra={
                "stream": stream,
                "entry_id": entry_id,
                "message_id": fields.get("message_id", ""),
            },
        )

    async def _emit_routing_outcome(
        self,
        *,
        outcome_type: str,
        project: str,
        task_id: str,
        agent_name: str = "",
        status: str,
        reason: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        project = project or "unknown"
        task_id = task_id or "unknown"
        fields: dict[str, str] = {
            "type": "brain.routing_outcome",
            "outcome_type": outcome_type,
            "project": project,
            "task_id": task_id,
            "agent_name": agent_name,
            "status": status,
            "reason": reason,
            "recorded_at": now_iso,
            "source": "agent:brain",
            "trace_id": get_current_trace_id() or "",
            "payload": json.dumps(extra or {}, default=str),
        }
        self.state.add_routing_outcome(
            RoutingOutcome(
                outcome_type=outcome_type,
                task_id=task_id,
                project=project,
                agent_name=agent_name,
                status=status,
                reason=reason,
                recorded_at=now_iso,
            )
        )
        self.state.events_by_type[f"routing_outcome.{outcome_type}"] = (
            self.state.events_by_type.get(f"routing_outcome.{outcome_type}", 0) + 1
        )
        if self._redis is None:
            return
        await self._redis.xadd(_BRAIN_OUTCOME_STREAM, fields, maxlen=5000, approximate=True)
        await self._redis.xadd(
            self._project_event_stream(project),
            fields,
            maxlen=5000,
            approximate=True,
        )

    # ------------------------------------------------------------------
    # Handler stubs (Sprint 1 — log + count only)
    # Sprint 3 adds scoring + ProviderMatrix dispatch.
    # ------------------------------------------------------------------

    async def _on_task_created(self, msg: dict) -> None:
        """Score a newly-created task, enqueue it, and emit task.scored.

        G64b project_id gate:
        1. Extract project_id from message metadata (raises MissingProjectIdError)
        2. Cross-check against stream key suffix (BLOCK-1)
        3. Gate via is_active (emit + skip if inactive)

        Scoring uses defaults for any field missing on the incoming
        task.created payload:
            impact=5.0, urgency=<priority>|"medium", unblock_count=0, cost=1.0
        """
        task_id = msg.get("task_id") or msg.get("id", "unknown")
        stream = msg.get("stream", "")
        msg_id = msg.get("entry_id", task_id)

        # --- G64b: project_id gate ----------------------------------------
        project_id = msg.get("project") or msg.get("project_id")

        if not project_id:
            raise MissingProjectIdError(
                f"task.created missing project field: task_id={task_id}, stream={stream}"
            )

        # BLOCK-1: cross-check project_id from metadata against stream key
        stream_project = extract_project_from_stream(stream)
        if stream_project is not None and stream_project != project_id:
            try:
                from sos.observability.sprint_telemetry import emit_project_id_stream_mismatch
                emit_project_id_stream_mismatch(project_id, stream_project, msg_id)
            except Exception:
                pass  # non-blocking fire-and-forget (WARN-1)
            logger.warning(
                "[brain] project_id mismatch: claimed=%r stream_key=%r task_id=%s; skipping",
                project_id, stream_project, task_id,
            )
            return

        # Gate: is project active?
        if not is_active(project_id):
            try:
                from sos.observability.sprint_telemetry import emit_inactive_project_filtered
                emit_inactive_project_filtered(project_id, msg_id, reason="not_in_active_set")
            except Exception:
                pass  # non-blocking fire-and-forget (WARN-1)
            logger.info(
                "[brain] project_id=%r not in active set; skipping task_id=%s",
                project_id, task_id,
            )
            return

        logger.info(
            "[brain] task.created task_id=%s title=%r project=%s",
            task_id,
            msg.get("title", ""),
            project_id,
        )
        self.state.tasks_in_flight.add(task_id)

        # --- Scoring (Sprint 2) -------------------------------------------
        impact: float = float(msg.get("impact", 5.0))
        urgency: str = msg.get("priority") or "medium"
        unblock_count: int = int(msg.get("unblock_count", 0))
        cost: float = float(msg.get("cost", 1.0))

        score: float = score_task(
            impact=impact,
            urgency=urgency,
            unblock_count=unblock_count,
            cost=cost,
        )

        # --- Record required skills for later dispatch matching -----------
        required_skills: list[str] = []
        raw_labels = msg.get("labels")
        if isinstance(raw_labels, list):
            required_skills = [str(lbl) for lbl in raw_labels if isinstance(lbl, str)]
        raw_skill_id = msg.get("skill_id")
        if isinstance(raw_skill_id, str) and raw_skill_id:
            # A single-string skill_id field is wrapped as a one-element list.
            required_skills = [raw_skill_id] if not required_skills else required_skills
        self.state.task_skills[task_id] = required_skills
        self.state.task_projects[task_id] = str(project_id)

        # Enqueue on the in-memory priority queue
        self.state.enqueue(task_id, score)

        # Attempt dispatch of the highest-score queued task (may be this one
        # or a previously-queued one that now has a matching agent).
        await self._try_dispatch_next()

        # --- Emit task.scored envelope ------------------------------------
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            scored = TaskScoredMessage(
                source="agent:brain",
                target="sos:channel:tasks",
                timestamp=now_iso,
                message_id=str(uuid.uuid4()),
                trace_id=get_current_trace_id(),
                payload=TaskScoredPayload(
                    task_id=task_id,
                    score=score,
                    urgency=urgency,  # type: ignore[arg-type]
                    impact=impact,
                    unblock_count=unblock_count,
                    cost=cost,
                    ts=now_iso,
                ),
            )
        except Exception:
            logger.exception("[brain] task.scored envelope construction failed task_id=%s", task_id)
            return

        assert self._redis is not None
        await self._redis.xadd(_BRAIN_EMIT_STREAM, scored.to_redis_fields())

    async def _on_task_completed(self, msg: dict) -> None:
        task_id = msg.get("task_id") or msg.get("id", "unknown")
        was_in_flight = task_id in self.state.tasks_in_flight
        agent_name = str(msg.get("agent") or msg.get("assignee") or "")
        logger.info("[brain] task.completed task_id=%s agent=%s", task_id, agent_name)
        self.state.tasks_in_flight.discard(task_id)
        self.state.unassign_task(agent_name, str(task_id))  # S062 Track A
        self.state.record_agent_success(agent_name)          # S062 Track A: decay failure penalty
        self.state.clear_task_retry(str(task_id))
        project = str(
            msg.get("project")
            or msg.get("project_id")
            or self.state.task_projects.get(str(task_id), "")
            or extract_project_from_stream(str(msg.get("stream", "")))
            or ""
        )
        self.state.task_projects.pop(str(task_id), None)
        self.state.task_skills.pop(str(task_id), None)
        await self._emit_routing_outcome(
            outcome_type="handoff_success" if was_in_flight else "completion_observed",
            project=project,
            task_id=str(task_id),
            agent_name=agent_name,
            status="completed",
            reason="task completed after brain-observed handoff" if was_in_flight else "task completed outside current brain in-flight set",
        )

    async def _on_task_failed(self, msg: dict) -> None:
        task_id = str(msg.get("task_id") or msg.get("id", "unknown"))
        agent_name = str(msg.get("agent") or msg.get("assignee") or "")
        reason = str(msg.get("reason") or msg.get("error") or "task failed")
        logger.warning("[brain] task.failed task_id=%s agent=%s reason=%r", task_id, agent_name, reason)
        self.state.tasks_in_flight.discard(task_id)
        self.state.unassign_task(agent_name, task_id)      # S062 Track A
        self.state.record_agent_failure(agent_name)         # S062 Track A: apply penalty
        project = str(
            msg.get("project")
            or msg.get("project_id")
            or self.state.task_projects.get(task_id, "")
            or extract_project_from_stream(str(msg.get("stream", "")))
            or ""
        )
        await self._emit_routing_outcome(
            outcome_type="handoff_failed",
            project=project,
            task_id=task_id,
            agent_name=agent_name,
            status="failed",
            reason=reason,
        )
        # S062 Track A: FSD — re-queue for rerouting, escalate after threshold
        retry_count = self.state.increment_task_retry(task_id)
        saved_skills = self.state.task_skills.get(task_id, [])
        if retry_count >= self.state.ESCALATION_THRESHOLD:
            logger.warning("[brain] task.escalated task_id=%s retries=%d", task_id, retry_count)
            self.state.task_projects.pop(task_id, None)
            self.state.task_skills.pop(task_id, None)
            self.state.clear_task_retry(task_id)
            await self._escalate_to_human(task_id, project, agent_name, reason, retry_count)
        else:
            # Re-queue with decayed score so other tasks can run first
            base_score = max(1.0, 10.0 / (retry_count + 1))
            self.state.enqueue(task_id, base_score)
            logger.info("[brain] task.requeued task_id=%s retry=%d score=%.2f", task_id, retry_count, base_score)

    async def _escalate_to_human(
        self, task_id: str, project: str, agent_name: str, reason: str, retry_count: int
    ) -> None:
        """FSD escalation: push a human-readable alert to the river bus stream (River owns the brain)."""
        text = (
            f"[BRAIN] Task `{task_id}` could not be routed after {retry_count} attempts. "
            f"Last agent: {agent_name or 'none'}. Reason: {reason}. "
            f"Project: {project}. Manual intervention required."
        )
        try:
            assert self._redis is not None
            await self._redis.xadd(
                _HUMAN_ESCALATION_STREAM,
                {
                    "type": "brain.escalation",
                    "source": "agent:brain",
                    "target": "agent:river",
                    "payload": json.dumps({"text": text}),
                    "task_id": task_id,
                    "project": project,
                    "retries": str(retry_count),
                },
            )
            logger.warning("[brain] escalation emitted task_id=%s", task_id)
        except Exception:
            logger.exception("[brain] escalation emit failed task_id=%s", task_id)

    async def _on_task_routed(self, msg: dict) -> None:
        """Stub: log. Sprint 3 generates these; for now we observe them."""
        logger.info(
            "[brain] task.routed task_id=%s agent=%r",
            msg.get("task_id", "unknown"),
            msg.get("agent", ""),
        )

    async def _on_agent_joined(self, msg: dict) -> None:
        """Log the join event and attempt to drain the priority queue.

        A new agent may unblock queued tasks whose required skills previously
        had no matching candidate.
        """
        logger.info(
            "[brain] agent_joined name=%r model=%r",
            msg.get("name") or msg.get("agent_name", "unknown"),
            msg.get("model", ""),
        )
        await self._try_dispatch_next()

    # ------------------------------------------------------------------
    # S064 Track A — Experience Ledger indexer
    # ------------------------------------------------------------------

    async def _on_experience_stored(self, fields: dict[str, str]) -> None:
        """Index an experience engram in a Redis sorted set for fast recall.

        Mirror emits experience.stored to experience:stream after a successful
        /experience/store call. BrainService maintains:
            ZADD experience:idx:{action_type}:{target_industry}
                 {metric_value} {engram_id}

        Hermes can then query the top-N by metric_value for a given
        action_type + target_industry combination without a DB round-trip.
        """
        engram_id = fields.get("engram_id", "")
        action_type = fields.get("action_type", "")
        target_industry = fields.get("target_industry", "")
        try:
            metric_value = float(fields.get("metric_value", "0"))
        except ValueError:
            metric_value = 0.0

        if not engram_id or not action_type or not target_industry:
            logger.warning(
                "[brain] experience.stored missing required fields: engram_id=%r action_type=%r industry=%r",
                engram_id, action_type, target_industry,
            )
            return

        idx_key = f"{_EXPERIENCE_IDX_PREFIX}:{action_type}:{target_industry}"
        assert self._redis is not None
        await self._redis.zadd(idx_key, {engram_id: metric_value})
        # Keep only top-50 per bucket — trim by lowest score
        await self._redis.zremrangebyrank(idx_key, 0, -51)

        logger.info(
            "[brain] experience indexed: engram=%s action=%s industry=%s metric=%.2f key=%s",
            engram_id, action_type, target_industry, metric_value, idx_key,
        )

    async def _attach_experience_context(
        self, task_id: str, required_skills: list[str]
    ) -> None:
        """Pre-fetch top-3 experience lessons and cache at experience:context:{task_id}.

        Only runs when 'outreach' or 'content' appears in required_skills.
        Reads from the ZADD sorted sets written by _on_experience_stored.
        TTL: 1 hour (task pickup window).  Best-effort — failure is logged and ignored.
        """
        action_types = [s for s in required_skills if s in ("outreach", "content", "ops")]
        if not action_types:
            return
        action_type = action_types[0]

        assert self._redis is not None
        # Scan known industry buckets for this action_type
        pattern = f"{_EXPERIENCE_IDX_PREFIX}:{action_type}:*"
        cursor: int = 0
        lessons: list[dict] = []
        try:
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern, count=50)
                for key in keys:
                    # Top entry per industry bucket
                    top = await self._redis.zrevrange(key, 0, 0, withscores=True)
                    if top:
                        engram_id, score = top[0]
                        industry = key.split(":")[-1]
                        lessons.append({
                            "engram_id": engram_id,
                            "action_type": action_type,
                            "target_industry": industry,
                            "metric_value": float(score),
                        })
                if cursor == 0:
                    break

            if not lessons:
                return

            # Sort by metric_value, keep top-3
            lessons.sort(key=lambda x: x["metric_value"], reverse=True)
            context_key = f"experience:context:{task_id}"
            await self._redis.setex(
                context_key,
                3600,
                json.dumps(lessons[:3]),
            )
            logger.info(
                "[brain] experience context attached task_id=%s action=%s lessons=%d",
                task_id, action_type, len(lessons[:3]),
            )
        except Exception:
            logger.exception(
                "[brain] experience context attach failed task_id=%s; skipping (non-fatal)",
                task_id,
            )

    # ------------------------------------------------------------------
    # Dispatch — match highest-score queued task to a registered agent
    # ------------------------------------------------------------------

    async def _try_dispatch_next(self) -> None:
        """Pop the highest-score task and try to route it to an agent.

        If no candidate has any skill overlap, the task is put back on the
        queue unchanged. On match, emits a task.routed event on the brain
        stream, moves the task to in_flight, and records the routing
        decision in BrainState.
        """
        if self.state.queue_size() == 0:
            return

        popped = self.state.pop_highest()
        if popped is None:
            return
        task_id, score = popped

        required_skills = self.state.task_skills.get(task_id, [])

        try:
            candidates = await _registry_client.list_agents()
        except Exception:
            logger.exception("[brain] registry list_agents failed; re-queueing task_id=%s", task_id)
            self.state.enqueue(task_id, score)
            return

        selected = select_agent(required_skills, candidates or [], self.state)
        if selected is None:
            # S062 Track A: FSD — escalate after threshold, otherwise re-queue
            retry_count = self.state.increment_task_retry(task_id)
            project = self.state.task_projects.get(task_id, "unknown")
            await self._emit_routing_outcome(
                outcome_type="dead_agent_route_skipped",
                project=project,
                task_id=task_id,
                agent_name="",
                status="skipped",
                reason="no registered agent matched required skills",
                extra={"required_skills": required_skills, "retry": retry_count},
            )
            if retry_count >= self.state.ESCALATION_THRESHOLD:
                logger.warning("[brain] task.escalated (no agent) task_id=%s retries=%d", task_id, retry_count)
                self.state.task_projects.pop(task_id, None)
                self.state.task_skills.pop(task_id, None)
                self.state.clear_task_retry(task_id)
                await self._escalate_to_human(task_id, project, "", "no agent with required skills", retry_count)
            else:
                self.state.enqueue(task_id, score)
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        normalized_score = min(score / 100.0, 1.0)
        overlap = len(set(required_skills) & set(selected.capabilities))

        try:
            routed = TaskRoutedMessage(
                source="agent:brain",
                target="sos:channel:tasks",
                timestamp=now_iso,
                message_id=str(uuid.uuid4()),
                trace_id=get_current_trace_id(),
                payload=TaskRoutedPayload(
                    task_id=task_id,
                    routed_to=selected.name,
                    routed_at=now_iso,
                    score=normalized_score,
                    reason=f"skill-match={overlap}",
                ),
            )
        except Exception:
            logger.exception(
                "[brain] task.routed envelope construction failed task_id=%s",
                task_id,
            )
            # Put the task back — we could not emit a valid routing decision.
            self.state.enqueue(task_id, score)
            return

        # S064 Track A — Experience Ledger pre-fetch.
        # If the task requires outreach skill, attach the top-3 lessons from
        # the Redis sorted set so Hermes finds them at experience:context:{task_id}.
        await self._attach_experience_context(task_id, required_skills)

        assert self._redis is not None
        await self._redis.xadd(_BRAIN_EMIT_STREAM, routed.to_redis_fields())
        await self._emit_routing_outcome(
            outcome_type="handoff_created",
            project=self.state.task_projects.get(task_id, "unknown"),
            task_id=task_id,
            agent_name=selected.name,
            status="routed",
            reason=f"skill-match={overlap}",
            extra={"score": score, "normalized_score": normalized_score, "required_skills": required_skills},
        )

        # State updates — record as in-flight + store decision for dashboard.
        self.state.tasks_in_flight.add(task_id)
        self.state.assign_task(selected.name, task_id)  # S062 Track A: wire agent load
        self.state.add_routing_decision(
            RoutingDecision(
                task_id=task_id,
                agent_name=selected.name,
                score=score,
                routed_at=now_iso,
            )
        )
