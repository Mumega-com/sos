"""Redis-backed build queue for per-tenant Astro builds.

Builds are queued when content changes. A worker process dequeues
and builds one tenant at a time. At 200 tenants with ~30s builds,
this handles ~10 builds/5min — more than enough for content-driven triggers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import redis

from sos.services.saas.builder import BuildOrchestrator

log = logging.getLogger("sos.saas.build_queue")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
QUEUE_KEY = "saas:build:queue"
PROCESSING_KEY = "saas:build:processing"
RESULTS_KEY = "saas:build:results"


class BuildQueue:
    def __init__(self) -> None:
        self.redis = redis.Redis.from_url(
            REDIS_URL, password=REDIS_PASSWORD, decode_responses=True
        )
        self.orchestrator = BuildOrchestrator()

    def enqueue(self, tenant_slug: str, trigger: str = "content_changed", priority: int = 0) -> str:
        """Add a build to the queue. Higher priority = built first."""
        job = {
            "tenant_slug": tenant_slug,
            "trigger": trigger,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "priority": priority,
        }
        # Use sorted set — score is negative priority (higher priority = lower score = dequeued first)
        # Tie-break on time
        score = -priority * 1_000_000 + time.time()
        self.redis.zadd(QUEUE_KEY, {json.dumps(job): score})
        log.info("Enqueued build for %s (trigger=%s, priority=%d)", tenant_slug, trigger, priority)
        return tenant_slug

    def dequeue(self) -> dict | None:
        """Pop the highest-priority build from the queue."""
        # Atomic pop from sorted set
        results = self.redis.zpopmin(QUEUE_KEY, count=1)
        if not results:
            return None
        job_json, _score = results[0]
        job = json.loads(job_json)
        # Mark as processing
        self.redis.set(f"{PROCESSING_KEY}:{job['tenant_slug']}", job_json, ex=300)
        return job

    def queue_length(self) -> int:
        return self.redis.zcard(QUEUE_KEY)

    def is_building(self, tenant_slug: str) -> bool:
        return self.redis.exists(f"{PROCESSING_KEY}:{tenant_slug}") > 0

    async def process_one(self) -> dict | None:
        """Dequeue and build one tenant. Returns build result."""
        job = self.dequeue()
        if not job:
            return None

        tenant_slug = job["tenant_slug"]
        log.info("Building %s (trigger=%s)", tenant_slug, job.get("trigger"))

        try:
            result = await self.orchestrator.build_tenant(tenant_slug, trigger=job.get("trigger", "queue"))
            # Store result
            self.redis.setex(
                f"{RESULTS_KEY}:{tenant_slug}",
                3600,  # 1 hour TTL
                json.dumps(result),
            )
            return result
        finally:
            # Clear processing lock
            self.redis.delete(f"{PROCESSING_KEY}:{tenant_slug}")

    async def worker_loop(self, poll_interval: float = 5.0) -> None:
        """Run forever, processing builds as they arrive."""
        log.info("Build queue worker started (poll every %.1fs)", poll_interval)
        while True:
            try:
                result = await self.process_one()
                if result:
                    status = "OK" if result.get("success") else "FAILED"
                    log.info("Build %s: %s", status, result.get("tenant", "unknown"))
                else:
                    await asyncio.sleep(poll_interval)
            except Exception as exc:
                log.error("Build worker error: %s", exc, exc_info=True)
                await asyncio.sleep(poll_interval)

    def get_status(self) -> dict:
        """Queue status for monitoring."""
        return {
            "queued": self.queue_length(),
            "processing": len(self.redis.keys(f"{PROCESSING_KEY}:*")),
        }
