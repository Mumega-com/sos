"""
Reputation Dreamer — scheduled Glicko-2 batch recompute hook (Sprint 004 A.1).

Constitutional role:
  This module is the ONLY caller of recompute_reputation_scores() in the
  Dreamer infrastructure. It forms the scheduled write path to reputation_state
  as mandated by §15 constitutional constraint 2.

Schedule:
  Default: every 3600s (1 hour). Override via SOS_REPUTATION_RECOMPUTE_INTERVAL.
  Each run processes all citizens with events in the last 7 days.
  Individual recompute: trigger(holder_id) for a single citizen (e.g., post-task).

Fail-open:
  Errors are logged and swallowed — a failed recompute leaves the prior state
  in place, which is always preferable to crashing the autonomy loop.

Kernel-private:
  σ (volatility) never surfaces here. The recompute writes raw state;
  callers read the VIEW (LCB = μ - 1.5·φ) or use get_state_raw() with kernel auth.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

log = logging.getLogger("sos.reputation_dreamer")

_DEFAULT_INTERVAL = 3600  # 1 hour, matching dream_interval_seconds in AutonomyService
_ENV_INTERVAL_KEY = "SOS_REPUTATION_RECOMPUTE_INTERVAL"


class ReputationDreamer:
    """
    Scheduled Glicko-2 recompute loop — Dreamer hook for §15 reputation state.

    Runs recompute_reputation_scores() on a configurable interval.
    Can be started as a background task alongside the main autonomy loop.

    Usage:
        dreamer = ReputationDreamer()
        asyncio.create_task(dreamer.start())
        # or for a single citizen after a task event:
        await dreamer.trigger(holder_id="citizen:abc")
    """

    def __init__(self, interval_seconds: Optional[float] = None) -> None:
        env_val = os.getenv(_ENV_INTERVAL_KEY)
        self.interval = (
            float(env_val)
            if env_val is not None
            else (interval_seconds if interval_seconds is not None else _DEFAULT_INTERVAL)
        )
        self.running = False
        self.cycle_count = 0
        self.last_run_at: Optional[float] = None
        self.last_stats: dict = {}

    async def start(self) -> None:
        """Start the recompute loop. Run as a background asyncio task."""
        self.running = True
        log.info("ReputationDreamer started", extra={"interval": self.interval})

        while self.running:
            try:
                await self._run_batch()
            except Exception as exc:
                log.error("Recompute batch failed — retaining prior state", exc_info=exc)
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break

        log.info("ReputationDreamer stopped")

    async def stop(self) -> None:
        self.running = False

    async def trigger(self, holder_id: str) -> dict:
        """
        Force an immediate single-citizen Glicko-2 recompute.

        Safe to call from async event handlers (e.g., task_completed webhook).
        Returns the stats dict from recompute_reputation_scores().
        """
        log.info("On-demand recompute", extra={"holder_id": holder_id})
        return await self._run_recompute(holder_id=holder_id)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _run_batch(self) -> None:
        self.cycle_count += 1
        log.info("Recompute batch cycle", extra={"cycle": self.cycle_count})
        stats = await self._run_recompute(holder_id=None)
        self.last_run_at = time.monotonic()
        self.last_stats = stats
        log.info(
            "Recompute batch complete",
            extra={
                "cycle": self.cycle_count,
                "holders": stats.get("holders", 0),
                "scores_written": stats.get("scores_written", 0),
            },
        )

    async def _run_recompute(self, *, holder_id: Optional[str]) -> dict:
        """Run the sync psycopg2 recompute in a thread executor to avoid blocking."""
        loop = asyncio.get_running_loop()
        from sos.contracts.reputation import recompute_reputation_scores

        def _sync() -> dict:
            return recompute_reputation_scores(holder_id=holder_id)

        return await loop.run_in_executor(None, _sync)

    def health(self) -> dict:
        return {
            "running": self.running,
            "cycle_count": self.cycle_count,
            "interval_seconds": self.interval,
            "last_run_at": self.last_run_at,
            "last_stats": self.last_stats,
        }
