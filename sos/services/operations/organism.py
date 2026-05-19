"""SOS Organism — the daily heartbeat that drives per-project pulses.

The organism is a long-running process (one per deployment) that:

1. Every ``sleep_seconds`` interval, checks the wall clock.
2. For each configured project, determines whether the current hour falls
   into one of three pulse windows (morning / noon / evening) and if a
   pulse for that window has not yet fired today, invokes the matching
   ``sos.services.operations.pulse.post_*`` coroutine.
3. Scans the audit stream (``sos:stream:global:objectives``) for recent
   ``state_transition=paid`` events on root-level objectives and posts a
   ``postmortem-<root_id>`` objective for each new one.
4. Logs and continues on every failure — the organism must outlive infra
   flakes (objectives service restart, redis blip, network hiccup).

State is tiny and Redis-backed:

- ``sos:organism:last_ran:{project}:{window}`` — presence flag (value is the
  ISO date it last ran), TTL 25h so each window fires at most once per day.
- ``sos:organism:postmortem_posted`` — Redis SET of root objective ids
  we've already posted a postmortem for; TTL 7d (expire_at rolling).

Run as a systemd unit — see ``docs/runbooks/sos-organism.service.md``.

CLI::

    python -m sos.services.operations.organism --projects trop
    python -m sos.services.operations.organism --projects trop,viamar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

from sos.clients.objectives import AsyncObjectivesClient
from sos.services.operations import pulse as pulse_mod

logger = logging.getLogger("sos.operations.organism")


# Morning 06:00-08:59, noon 11:00-13:59, evening 18:00-20:59 (server local).
_WINDOW_HOURS: dict[str, range] = {
    "morning": range(6, 9),
    "noon": range(11, 14),
    "evening": range(18, 21),
}

_POSTMORTEM_SET_KEY = "sos:organism:postmortem_posted"
_POSTMORTEM_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days
_WINDOW_TTL_SECONDS = 25 * 60 * 60  # 25 hours
_AUDIT_STREAM = "sos:stream:global:objectives"


# ---------------------------------------------------------------------------
# Window detection + cache helpers
# ---------------------------------------------------------------------------


def _current_window(now: datetime | None = None) -> str | None:
    """Return the current pulse window name or ``None``.

    Uses local server time — the same clock systemd and the operator see.
    Passing ``now`` is a test seam; production calls with the default.
    """
    now = now or datetime.now()
    hour = now.hour
    for window, hours in _WINDOW_HOURS.items():
        if hour in hours:
            return window
    return None


def _window_key(project: str, window: str) -> str:
    return f"sos:organism:last_ran:{project}:{window}"


def _seen_today(project: str, window: str, redis_client: Any) -> bool:
    """True if the ``(project, window)`` pulse already fired today.

    Reads the Redis cache key. Fail-soft: any exception from Redis returns
    ``False`` (we'd rather double-fire a pulse than silently skip a day).
    """
    try:
        raw = redis_client.get(_window_key(project, window))
    except Exception as exc:
        logger.debug(
            "organism: last-ran read failed for %s/%s: %s",
            project,
            window,
            exc,
        )
        return False
    if raw is None:
        return False
    # Value is the ISO date of the last run; if that equals today → seen.
    today = datetime.now().strftime("%Y-%m-%d")
    return str(raw) == today


def _mark_seen(project: str, window: str, redis_client: Any) -> None:
    """Mark ``(project, window)`` as fired today with a 25h TTL."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        redis_client.set(
            _window_key(project, window),
            today,
            ex=_WINDOW_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "organism: last-ran write failed for %s/%s: %s",
            project,
            window,
            exc,
        )


# ---------------------------------------------------------------------------
# Redis client factory — matches kill_switch pattern
# ---------------------------------------------------------------------------


def _resolve_redis_url(redis_url: str | None = None) -> str:
    if redis_url:
        return redis_url
    url = os.environ.get("REDIS_URL") or os.environ.get("SOS_REDIS_URL")
    if url:
        return url
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


def _get_redis_client(redis_url: str | None = None):  # type: ignore[no-untyped-def]
    """Build a fresh sync Redis client. Lazy import keeps the module optional."""
    import redis  # type: ignore[import-untyped]

    url = _resolve_redis_url(redis_url)
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


# ---------------------------------------------------------------------------
# Postmortem-on-paid-root scanning
# ---------------------------------------------------------------------------


def _extract_paid_root_events(
    entries: Iterable[tuple[str, dict[str, Any]]],
) -> list[tuple[str, str | None]]:
    """Pluck ``(objective_id, project)`` for each paid-root transition.

    Accepts the XRANGE shape: an iterable of ``(stream_id, fields_dict)``
    where ``fields_dict["payload"]`` is the JSON string written by
    :func:`sos.services.objectives.app._emit_audit`.

    Two payload shapes exist in the wild:
    1. Flat: ``{"id":..., "new_state":"paid", ...}``
    2. Wrapped (paid-transition block in objectives/app.py): ``{"type":...,
       "payload":{"id":..., "new_state":"paid", ...}}``

    We normalise both. ``parent_id`` is NOT reliably in the payload — we
    return only candidate paid ids and let the caller decide via a followup
    ``client.get()`` whether it's actually a root.
    """
    results: list[tuple[str, str | None]] = []
    for _stream_id, fields in entries:
        try:
            raw = fields.get("payload")
            if not raw:
                continue
            data = json.loads(raw)
        except Exception:
            continue
        # Unwrap one level if wrapped.
        if isinstance(data, dict) and "payload" in data and isinstance(data["payload"], dict):
            data = data["payload"]
        if not isinstance(data, dict):
            continue
        if data.get("new_state") != "paid":
            continue
        obj_id = data.get("id")
        if not obj_id:
            continue
        project = data.get("project")
        results.append((str(obj_id), project if isinstance(project, str) else None))
    return results


async def _maybe_post_postmortem(
    obj_id: str,
    project: str | None,
    *,
    client: AsyncObjectivesClient,
    redis_client: Any,
) -> bool:
    """Post a postmortem for ``obj_id`` if it's a root and not yet done.

    Returns True if a postmortem was posted this call. Fail-soft.
    """
    # De-dupe: have we already posted a postmortem for this root?
    try:
        already = redis_client.sismember(_POSTMORTEM_SET_KEY, obj_id)
    except Exception as exc:
        logger.debug("organism: postmortem dedupe read failed: %s", exc)
        already = False
    if already:
        return False

    # Confirm it's a root by fetching the objective. Non-roots or missing →
    # skip, but still mark as seen so we don't re-check the same id on every
    # tick.
    is_root = False
    try:
        obj = await client.get(obj_id, project=project)
        if obj is not None and not obj.parent_id:
            is_root = True
    except Exception as exc:
        logger.debug("organism: get(%s) failed: %s", obj_id, exc)
        # Can't confirm root status — skip without marking, we may see
        # the event again in a later tick when the service is back.
        return False

    if not is_root:
        try:
            redis_client.sadd(_POSTMORTEM_SET_KEY, obj_id)
            redis_client.expire(_POSTMORTEM_SET_KEY, _POSTMORTEM_TTL_SECONDS)
        except Exception:
            pass
        return False

    # Post the postmortem objective.
    try:
        await client.create(
            title=f"postmortem-{obj_id}",
            description=(
                f"Automated postmortem for paid root objective {obj_id}. "
                f"Summarize what shipped, what moved the needle, and what "
                f"should feed the demo bank."
            ),
            bounty_mind=150,
            tags=[
                "postmortem",
                "kind:postmortem",
                f"parent_of_postmortem:{obj_id}",
            ],
            capabilities_required=["postmortem"],
            subscribers=[],
            tenant_id=project or "default",
            project=project,
            created_by="organism:postmortem",
        )
    except Exception as exc:
        logger.warning("organism: failed to post postmortem for %s: %s", obj_id, exc)
        return False

    try:
        redis_client.sadd(_POSTMORTEM_SET_KEY, obj_id)
        redis_client.expire(_POSTMORTEM_SET_KEY, _POSTMORTEM_TTL_SECONDS)
    except Exception as exc:
        logger.debug("organism: postmortem dedupe write failed: %s", exc)

    logger.info("organism: posted postmortem for paid root %s", obj_id)
    return True


# ---------------------------------------------------------------------------
# Organism class
# ---------------------------------------------------------------------------


class Organism:
    """The daily heartbeat.

    Construct with one or more project slugs, then call :meth:`run_forever`
    from a systemd-managed process. :meth:`step` is exposed as a single
    iteration for tests and manual one-shot runs.
    """

    def __init__(
        self,
        *,
        projects: list[str],
        objectives_url: str | None = None,
        objectives_token: str | None = None,
        sleep_seconds: int = 60,
        redis_client: Any = None,
        client: AsyncObjectivesClient | None = None,
    ) -> None:
        if not projects:
            raise ValueError("Organism requires at least one project")
        self._projects = list(projects)
        self._sleep_seconds = max(1, int(sleep_seconds))
        self._objectives_url = objectives_url
        self._objectives_token = objectives_token
        # Test seams — production leaves both None and lazy-constructs.
        self._redis_override = redis_client
        self._client_override = client

    # ------------------------------------------------------------------
    # Internal factories — re-resolve each tick so a mid-run env change
    # (secret rotated, url pointed at a new host) takes effect without
    # restarting the service.
    # ------------------------------------------------------------------

    def _build_client(self) -> AsyncObjectivesClient:
        if self._client_override is not None:
            return self._client_override
        return AsyncObjectivesClient(
            base_url=self._objectives_url,
            token=self._objectives_token,
        )

    def _build_redis(self):  # type: ignore[no-untyped-def]
        if self._redis_override is not None:
            return self._redis_override
        return _get_redis_client()

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def step(self) -> None:
        """Run one iteration. Fail-soft on all branches."""
        window = _current_window()
        client = self._build_client()
        redis_client = self._build_redis()

        # 1. Per-project pulse for the current window.
        if window is not None:
            pulse_fn = _pulse_for_window(window)
            for project in self._projects:
                try:
                    if _seen_today(project, window, redis_client):
                        continue
                    logger.info(
                        "organism: firing %s pulse for project=%s",
                        window,
                        project,
                    )
                    root_id = await pulse_fn(project, client=client)
                    if root_id:
                        _mark_seen(project, window, redis_client)
                    else:
                        logger.info(
                            "organism: pulse returned empty root "
                            "(project=%s, window=%s) — will retry next tick",
                            project,
                            window,
                        )
                except Exception as exc:
                    logger.warning(
                        "organism: pulse failed for project=%s window=%s: %s",
                        project,
                        window,
                        exc,
                    )

        # 2. Scan audit stream for paid roots — post postmortems.
        try:
            await self._scan_paid_roots(client=client, redis_client=redis_client)
        except Exception as exc:
            logger.warning("organism: paid-root scan failed: %s", exc)

    async def run_forever(self) -> None:
        """Run :meth:`step` forever with ``sleep_seconds`` between iterations.

        Intended entry point for systemd. Never raises; every tick is wrapped.
        """
        logger.info(
            "organism: starting run_forever (projects=%s, sleep=%ds)",
            self._projects,
            self._sleep_seconds,
        )
        while True:
            try:
                await self.step()
            except Exception as exc:  # pragma: no cover — step() is itself
                # already fail-soft, this is belt-and-braces for exotic
                # bugs like MemoryError or KeyboardInterrupt wrapped in Task.
                logger.exception("organism: step() raised unexpectedly: %s", exc)
            await asyncio.sleep(self._sleep_seconds)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _scan_paid_roots(
        self,
        *,
        client: AsyncObjectivesClient,
        redis_client: Any,
    ) -> None:
        """Read the audit stream and post a postmortem for each new paid root.

        We lookback ``sleep_seconds * 2`` in wall-clock ms to avoid missing
        events that landed during the previous sleep. The dedupe set is the
        authority on "already handled".
        """
        lookback_ms = self._sleep_seconds * 2 * 1000
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = max(0, now_ms - lookback_ms)
        try:
            entries = redis_client.xrange(
                _AUDIT_STREAM,
                min=str(start_ms),
                max="+",
                count=200,
            )
        except Exception as exc:
            logger.debug("organism: xrange audit failed: %s", exc)
            return

        paid = _extract_paid_root_events(entries or [])
        for obj_id, project in paid:
            try:
                await _maybe_post_postmortem(
                    obj_id,
                    project,
                    client=client,
                    redis_client=redis_client,
                )
            except Exception as exc:
                logger.warning(
                    "organism: postmortem handling failed for %s: %s",
                    obj_id,
                    exc,
                )


def _pulse_for_window(window: str):
    """Map a window name to its pulse coroutine."""
    if window == "morning":
        return pulse_mod.post_morning_pulse
    if window == "noon":
        return pulse_mod.post_noon_pulse
    if window == "evening":
        return pulse_mod.post_evening_pulse
    raise ValueError(f"unknown pulse window: {window!r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sos.services.operations.organism",
        description="Run the SOS organism — per-project daily pulses.",
    )
    p.add_argument(
        "--projects",
        required=True,
        help=(
            "Comma-separated list of project slugs to service, e.g. "
            "'trop,viamar'. At least one is required."
        ),
    )
    p.add_argument(
        "--sleep-seconds",
        type=int,
        default=60,
        help="Seconds between ticks. Default 60.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = _parse_args(argv)
    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    if not projects:
        logger.error("organism: --projects must include at least one slug")
        return 2
    organism = Organism(
        projects=projects,
        sleep_seconds=args.sleep_seconds,
    )
    try:
        asyncio.run(organism.run_forever())
    except KeyboardInterrupt:
        logger.info("organism: interrupted, shutting down")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
