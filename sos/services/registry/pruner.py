"""Heartbeat pruner — stale/remove lifecycle for enrolled mesh agents.

HeartbeatPruner runs as a background asyncio task inside the registry service.
Every ``interval_seconds`` it scans all AgentCards across every project and:

- If ``age >= remove_after``: deletes the Redis key outright.
- If ``age >= stale_after`` and not already stale: rewrites the card with
  ``stale=True`` and a reduced TTL equal to the remaining remove window.
- Otherwise: no-op.

The ``clock`` injectable is a ``Callable[[], datetime]`` that defaults to
``datetime.now(timezone.utc)`` — tests advance it manually.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional

from sos.observability.logging import get_logger
from sos.services.registry import _get_redis, write_card

log = get_logger("sos.registry.pruner")


class HeartbeatPruner:
    """Scans enrolled AgentCards and applies the stale/remove lifecycle."""

    def __init__(
        self,
        *,
        interval_seconds: int = 60,
        stale_after: int = 300,
        remove_after: int = 900,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.stale_after = stale_after
        self.remove_after = remove_after
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self._stop_event = asyncio.Event()
        self.staled_count: int = 0
        self.removed_count: int = 0

    def _now(self) -> datetime:
        return self._clock()

    def stop(self) -> None:
        """Signal the run loop to exit after the current sleep."""
        self._stop_event.set()

    async def run(self) -> None:
        """Loop: sleep ``interval_seconds``, then call ``_tick``."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.sleep(self.interval_seconds)),
                    timeout=self.interval_seconds + 1,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                break
            if self._stop_event.is_set():
                break
            await self._tick()

    async def _tick(self) -> None:
        """Scan all cards and apply stale/remove transitions."""
        # System-level sweep: project=None → read_all_cards returns cards from
        # all projects when the registry is configured that way.  We supplement
        # by iterating known project-prefixed keys via Redis directly.
        try:
            r = _get_redis()
        except Exception as exc:
            log.warn("pruner: redis unavailable, skipping tick", error=str(exc))
            return

        now = self._now()

        # Gather all card keys across every project prefix.
        try:
            all_keys: list[str] = r.keys("sos:cards:*")
        except Exception as exc:
            log.warn("pruner: failed to scan card keys", error=str(exc))
            return

        for key in all_keys:
            try:
                data: dict[str, str] = r.hgetall(key)
                if not data:
                    continue

                # Parse last_seen; skip gracefully on malformed values.
                last_seen_raw = data.get("last_seen", "")
                try:
                    last_seen = datetime.fromisoformat(last_seen_raw.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    log.warn(
                        "pruner: malformed last_seen, skipping key",
                        key=key,
                        last_seen=last_seen_raw,
                    )
                    continue

                age = (now - last_seen).total_seconds()

                if age >= self.remove_after:
                    r.delete(key)
                    self.removed_count += 1
                    log.info(
                        "pruner: removed stale card",
                        key=key,
                        age_seconds=int(age),
                    )
                    continue

                if age >= self.stale_after:
                    # Only re-write if not already stale to avoid churn.
                    already_stale = data.get("stale", "false").lower() == "true"
                    if already_stale:
                        continue

                    from sos.contracts.agent_card import AgentCard

                    try:
                        card = AgentCard.from_redis_hash(data)
                    except Exception as exc:
                        log.warn(
                            "pruner: failed to parse card, skipping",
                            key=key,
                            error=str(exc),
                        )
                        continue

                    # Use card.project for the write_card scope (mirrors the
                    # key pattern: sos:cards[:<project>]:<name>).
                    project: Optional[str] = card.project

                    remaining_ttl = int(self.remove_after - age)
                    stale_card = card.model_copy(update={"stale": True})
                    write_card(stale_card, project=project, ttl_seconds=remaining_ttl)
                    self.staled_count += 1
                    log.info(
                        "pruner: marked card stale",
                        key=key,
                        age_seconds=int(age),
                        remaining_ttl=remaining_ttl,
                    )

            except Exception as exc:
                log.warn(
                    "pruner: unexpected error processing key",
                    key=key,
                    error=str(exc),
                )
