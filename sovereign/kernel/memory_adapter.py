"""
MirrorMemoryAdapter — MemoryPort implementation backed by the Mirror HTTP API.

Kills the K1 finding from #267 (brain microkernel extraction, task #3):
`sovereign/brain.py` was writing/reading memory via raw `requests.post`
against Mirror's `/store` and `/search` endpoints, with zero use of the
canonical `sos.contracts.ports.memory.MemoryPort` contract. This adapter is
the routing point — brain.py's memory I/O goes through here instead.

Design decision — MemoryPort, not MemoryContract:
    Two memory contracts exist in this repo:
      - `sos.contracts.ports.memory.MemoryPort` — a 3-method Protocol
        (remember/recall/search) with a generic {content, metadata} /
        {query, filters} envelope. Cross-language (Inkwell has a TS mirror).
      - `sos.contracts.memory.MemoryContract` — a 10-method ABC (store, search,
        get, delete, relate, consolidate, decay, health, stats) with
        capability-scoped, tenant-aware semantics. A full internal memory
        service surface.
    brain.py's actual call sites (see K1 grep) only ever do two things against
    Mirror: write an engram (`/store`) and semantic-search engrams (`/search`).
    They never delete, relate, decay, or fetch stats. Implementing the full
    MemoryContract ABC here would mean either faking 6 unused methods (delete/
    relate/consolidate/decay/health/stats) against endpoints this adapter has
    no reason to call, or reaching into Mirror surface area explicitly marked
    Slice 2 territory (dream_consolidate/tend_goal_progress already carry
    `TODO(Slice 2 / DreamerPort)` markers for `/consolidate` and the tier PATCH
    endpoint — see brain.py). That would be scope creep past this K1 slice.
    MemoryPort's shape is a 1:1 match for what brain.py's hot path (remember /
    hippocampus_recall / motor_execute post_content) does. Verdict: adapt
    against MemoryPort, fold Mirror's richer wire fields into `metadata` /
    `filters` per the port's own escape hatch (both are typed as
    `Optional[dict[str, Any]]` specifically to carry backend-specific shape).

Wire-format translation (Mirror's /store and /search payloads carry fields
MemoryPort's generic envelope has no named slot for — agent, context_id,
core_concepts, raw_data, agent_filter, top_k):
    - remember(): `agent`/`context_id`/`core_concepts`/`raw_data` fold into
      `RememberRequest.metadata` on write.
    - search()/recall(): `top_k`/`agent_filter` fold into `SearchRequest.filters`
      on the request side. On the response side, Mirror's `raw_data` field is
      unpacked back out into `MemoryResult.metadata` so callers written against
      the old `result.get("raw_data", {})` shape (brain.py's hippocampus_recall)
      keep working via `hit.metadata` without knowing the wire format changed.

Sync bridge: `sos.contracts.ports.memory.MemoryPort` declares `async def`
signatures (shared with Inkwell's TS mirror), but the brain is a plain
synchronous daemon (sovereign/brain.py runs a blocking cycle() loop, no event
loop). `remember_sync()` / `search_sync()` are the methods brain.py actually
calls; `remember()` / `recall()` / `search()` are thin `asyncio.to_thread()`
wrappers kept so the adapter also satisfies the MemoryPort Protocol shape
for any future async consumer (isinstance-checkable via `runtime_checkable`).

fail-open contract: every method returns a safe empty result on any
exception — the brain's fail-open guarantees (see brain.py module docstring)
must not be undone by routing through this adapter.

Invariant: no raw `requests.post(MIRROR_URL/store or /search)` in brain.py's
hot path (hippocampus_recall / remember / motor_execute post_content) — all
three route through this adapter as of this change. Non-memory Mirror calls
(`/tasks`) are a task-board concern (a different port, out of scope for K1).
Dreamer maintenance paths (`_mirror_search`, `tend_goal_progress`,
`dream_consolidate`) are NOT ported — explicitly Slice 2 (DreamerPort).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import requests
from kernel.clients import MirrorClient

from sos.contracts.ports.memory import (
    MemoryResult,
    RecallRequest,
    RememberRequest,
    RememberResult,
    SearchRequest,
)

logger = logging.getLogger("kernel.memory_adapter")


class MirrorMemoryAdapter:
    """MemoryPort-shaped adapter over Mirror's HTTP API.

    Wraps kernel.clients.MirrorClient, which already handles auth headers,
    base-URL config, and fail-open error handling for /store. The adapter
    translates MemoryPort call shapes to Mirror's /store and /search wire
    formats (see module docstring for the field-folding rules).
    """

    def __init__(
        self,
        *,
        client: Optional[MirrorClient] = None,
        agent_tag: str = "brain",
    ) -> None:
        self._client = client or MirrorClient()
        self._agent_tag = agent_tag

    # -- synchronous helpers (the brain is a sync daemon) --------------------

    def remember_sync(
        self,
        content: str,
        *,
        context_id: Optional[str] = None,
        core_concepts: Optional[list[str]] = None,
        raw_data: Optional[dict[str, Any]] = None,
    ) -> RememberResult:
        """Store an engram via Mirror /store.

        Maps to MemoryPort.remember() but synchronous — no asyncio.run()
        overhead, since brain.py calls this from plain (non-async) functions.

        Mirror payload contract:
          agent         (str)  — who is writing
          text          (str)  — the content
          context_id    (str)  — idempotency / topic key
          core_concepts (list) — tags for recall
          raw_data      (dict) — structured metadata (optional)
        """
        payload: dict[str, Any] = {
            "agent": self._agent_tag,
            "text": content,
            "context_id": context_id or f"{self._agent_tag}_mem_{int(time.time())}",
        }
        if core_concepts is not None:
            payload["core_concepts"] = core_concepts
        if raw_data is not None:
            payload["raw_data"] = raw_data

        try:
            result = self._client.store(payload)
        except Exception as exc:  # fail-open: callers must not see this raise
            logger.warning("MirrorMemoryAdapter.remember_sync: store raised: %s", exc)
            result = {}
        mem_id: str = str(result.get("id") or result.get("engram_id") or "unknown")
        return RememberResult(memory_id=mem_id)

    def search_sync(
        self,
        query: str,
        *,
        top_k: int = 10,
        agent_filter: Optional[str] = None,
    ) -> list[MemoryResult]:
        """Search Mirror via /search.

        Maps to MemoryPort.search() but synchronous.

        Deliberately does NOT go through `MirrorClient.search()` — that
        convenience method has a different signature (`threshold`,
        `workspace_id`, no `agent_filter`) built for a different caller.
        Routing through it would silently drop `agent_filter` (changing it
        from a server-side filter to nothing) and add an unrequested
        `threshold` field, breaking the "behavior-identical" requirement.
        This adapter is the one place allowed to touch Mirror's /search wire
        format directly, using the same base_url/auth as MirrorClient.

        agent_filter maps 1:1 to Mirror's `agent_filter` field so callers
        that previously passed `"agent_filter": "os"` retain exact same
        server-side semantics. Mirror's `raw_data` field is unpacked into
        `MemoryResult.metadata` so `hit.metadata` reproduces the old
        `result.get("raw_data", {})` access pattern.
        """
        body: dict[str, Any] = {"query": query, "top_k": top_k}
        if agent_filter is not None:
            body["agent_filter"] = agent_filter

        try:
            # Reuses MirrorClient's private _headers() rather than
            # duplicating the auth-header construction here. Scope rule for
            # this change forbids editing kernel/clients.py to add a public
            # accessor, so this stays a deliberate, commented exception.
            r = requests.post(
                f"{self._client.base_url}/search",
                json=body,
                headers=self._client._headers(),
                timeout=10,
            )
            r.raise_for_status()
            raw = r.json()
        except Exception as exc:
            logger.warning("MirrorMemoryAdapter.search_sync failed: %s", exc)
            return []

        if isinstance(raw, dict):
            hits = raw.get("results", []) or raw.get("memories", [])
        elif isinstance(raw, list):
            hits = raw
        else:
            return []

        out: list[MemoryResult] = []
        for h in hits:
            if not isinstance(h, dict):
                continue
            raw_score = h.get("score")
            # MemoryResult.score is constrained to [0.0, 1.0] (pydantic
            # ge/le). Mirror's score is not contractually bounded to that
            # range — clamp rather than let a validation error break the
            # fail-open contract for the whole batch.
            score: Optional[float] = None
            if raw_score is not None:
                try:
                    score = max(0.0, min(1.0, float(raw_score)))
                except (TypeError, ValueError):
                    score = None
            try:
                out.append(
                    MemoryResult(
                        id=str(h.get("id") or h.get("engram_id") or ""),
                        content=str(h.get("text") or h.get("content") or ""),
                        created_at=str(h.get("created_at") or ""),
                        metadata=h.get("raw_data") or h.get("metadata"),
                        score=score,
                    )
                )
            except Exception as exc:  # fail-open per-hit, don't drop the batch
                logger.warning("MirrorMemoryAdapter.search_sync: skipping malformed hit: %s", exc)
                continue
        return out

    # -- async Protocol methods (MemoryPort shape, for non-brain consumers) --

    async def remember(self, req: RememberRequest) -> RememberResult:
        """Async MemoryPort.remember() — delegates to remember_sync()."""
        metadata = req.metadata or {}
        return await asyncio.to_thread(
            self.remember_sync,
            req.content,
            context_id=metadata.get("context_id"),
            core_concepts=metadata.get("core_concepts"),
            raw_data=metadata.get("raw_data"),
        )

    async def recall(self, req: RecallRequest) -> list[MemoryResult]:
        """Async MemoryPort.recall() — delegates to search_sync()."""
        return await asyncio.to_thread(
            self.search_sync, req.query, top_k=req.limit or 10
        )

    async def search(self, req: SearchRequest) -> list[MemoryResult]:
        """Async MemoryPort.search() — delegates to search_sync()."""
        filters = req.filters or {}
        return await asyncio.to_thread(
            self.search_sync,
            req.query,
            agent_filter=filters.get("agent_filter"),
            top_k=filters.get("top_k", 10),
        )


# ---------------------------------------------------------------------------
# Module-level singleton — brain.py imports this; all modules that do
# `from kernel.memory_adapter import memory` share the same instance.
# ---------------------------------------------------------------------------

memory: MirrorMemoryAdapter = MirrorMemoryAdapter(agent_tag="brain")
