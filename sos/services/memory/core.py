"""MemoryCore — SOS memory backed by Mirror kernel directly. No HTTP client.

Async interface is preserved for compatibility with existing callers in app.py.
Sync Mirror DB calls are offloaded to a thread pool so the FastAPI event loop
is never blocked.
"""
from __future__ import annotations

import sys
import asyncio
import concurrent.futures
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Mirror kernel is at /home/mumega — add to path if not already present
if '/home/mumega' not in sys.path:
    sys.path.insert(0, '/home/mumega')

from mirror.kernel.db import get_db
from mirror.kernel.embeddings import get_embedding

from sos.services.memory.monitor import CoherenceMonitor

log = logging.getLogger("memory_core")

# Shared thread pool for blocking Mirror DB calls
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="memory-core",
)


@dataclass
class MemoryItem:
    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class MemoryCore:
    """
    The Hippocampus of the Sovereign OS.
    Backed by the Mirror kernel directly — no HTTP, same connection pool.
    Exposes async methods (offloaded to thread pool) to stay compatible with
    FastAPI callers in app.py.
    """

    def __init__(self, agent_name: str = "sos"):
        self.agent = agent_name
        self.monitor = CoherenceMonitor()

        try:
            # get_db() returns a LocalDB (or SupabaseDB) instance.
            # Call it once and hold a reference — avoids spawning extra pools.
            self._db = get_db()
            log.info("MemoryCore: Mirror kernel loaded (direct, no HTTP) for agent=%s", agent_name)
        except Exception as exc:
            log.warning(
                "MemoryCore: Mirror DB unavailable: %s — memory degraded", exc
            )
            self._db = None

    # ── sync internals (run in thread pool) ────────────────────────────────

    def _sync_add(self, content: str, metadata: dict) -> str:
        if self._db is None:
            return ""
        from datetime import datetime, timezone
        context_id = metadata.get(
            "context_id",
            f"{self.agent}:{hash(content) & 0xFFFFFFFF}",
        )
        embedding = [float(x) for x in get_embedding(content)]
        workspace = metadata.get("project", "sos")
        self._db.upsert_engram({
            "context_id": context_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "series": f"{self.agent.title()} - SOS",
            "workspace_id": workspace,
            "project": workspace,
            "epistemic_truths": metadata.get("tags", []),
            "core_concepts": metadata.get("tags", []),
            "affective_vibe": metadata.get("vibe", "Neutral"),
            "energy_level": "Balanced",
            "next_attractor": "",
            "raw_data": {"agent": self.agent, "text": content, **metadata},
            "embedding": embedding,
        })
        return context_id

    def _sync_search(
        self, query: str, limit: int, project: Optional[str]
    ) -> List[MemoryItem]:
        if self._db is None:
            return []
        workspace_id = project or "sos"
        embedding = [float(x) for x in get_embedding(query)]
        rows = self._db.search_engrams(embedding, 0.5, limit, workspace_id, workspace_id)
        results = []
        for r in rows:
            raw = r.get("raw_data") or {}
            text = raw.get("text", "") if isinstance(raw, dict) else ""
            if not text:
                text = str(r.get("context_id", ""))
            results.append(MemoryItem(
                id=str(r.get("context_id", "")),
                content=text,
                metadata=raw if isinstance(raw, dict) else {},
                score=float(r.get("similarity", 0.0)),
            ))
        return results

    def _sync_search_code(
        self, query: str, limit: int, repo: Optional[str]
    ) -> List[MemoryItem]:
        if self._db is None:
            return []
        embedding = [float(x) for x in get_embedding(query)]
        rows = self._db.search_code_nodes(embedding, 0.5, limit, repo, None)
        return [
            MemoryItem(
                id=str(r.get("id", "")),
                content=r.get("text", "") or r.get("signature", ""),
                metadata=dict(r),
                score=float(r.get("similarity", 0.0)),
            )
            for r in rows
        ]

    # ── async interface (compatible with existing callers) ──────────────────

    async def add(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Add a memory engram. Returns the context_id."""
        metadata = metadata or {}

        # Coherence check (non-blocking — uses the same search path)
        try:
            results = await self.search(content, limit=1)
            best_score = results[0].score if results else 0.5
            state = self.monitor.update(best_score)
            log.info(
                "ARF State | score=%.4f alpha=%.4f regime=%s",
                best_score, state.alpha_norm, state.regime,
            )
        except Exception as exc:
            log.warning("Coherence check failed: %s", exc)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, lambda: self._sync_add(content, metadata))

    async def search(
        self, query: str, limit: int = 5, project: Optional[str] = None
    ) -> List[MemoryItem]:
        """Semantic search for memories."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor, lambda: self._sync_search(query, limit, project)
        )

    async def search_code(
        self, query: str, limit: int = 5, repo: Optional[str] = None
    ) -> List[MemoryItem]:
        """Semantic search over the code graph."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor, lambda: self._sync_search_code(query, limit, repo)
        )

    async def get_arf_state(self) -> Dict[str, Any]:
        """Return the current ARF (Alpha Drift / coherence) state."""
        state = self.monitor.get_state()
        return {
            "alpha_drift": state.alpha_norm,
            "regime": state.regime,
            "coherence_raw": state.coherence,
            "timestamp": state.timestamp,
        }

    async def health(self) -> Dict[str, Any]:
        """Return health status of the MemoryCore."""
        db_ok = self._db is not None
        # Quick connectivity check — count engrams (cheap, no embedding)
        if db_ok:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(_executor, lambda: self._db.count_engrams())
            except Exception as exc:
                log.warning("MemoryCore health check DB ping failed: %s", exc)
                db_ok = False

        return {
            "status": "ok" if db_ok else "degraded",
            "backend": "mirror_kernel_direct",
            "mirror_connected": db_ok,
            "monitor": self.monitor.get_state().regime,
        }

    # ── sync aliases (for non-async callers, e.g. direct scripts) ──────────

    def remember(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Sync alias for add()."""
        return self._sync_add(content, metadata or {})

    def recall(
        self, query: str, limit: int = 5, project: Optional[str] = None
    ) -> List[MemoryItem]:
        """Sync alias for search()."""
        return self._sync_search(query, limit, project)
