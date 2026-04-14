import os
import time
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from dotenv import load_dotenv

from sos.kernel import Config
from sos.observability.logging import get_logger
from sos.services.memory.monitor import CoherenceMonitor
from sos.clients.mirror import MirrorClient

log = get_logger("memory_core")

@dataclass
class MemoryItem:
    id: str
    content: str
    metadata: Dict[str, Any]
    score: float = 0.0

class MemoryCore:
    """
    The Hippocampus of the Sovereign OS.
    Acts as a Proxy to the Unified Mirror API (Local PGVector).
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.monitor = CoherenceMonitor()
        
        # Load environment from SOS root
        env_path = Path(__file__).parents[3] / ".env"
        log.info(f"Loading .env from {env_path} (exists: {env_path.exists()})")
        load_dotenv(dotenv_path=env_path)
        
        # Initialize Mirror Client
        mirror_url = os.getenv("MIRROR_URL", "http://localhost:8844")
        agent_name = os.getenv("SOS_AGENT_NAME", "sos")
        self.mirror = MirrorClient(base_url=mirror_url, agent_id=agent_name)
        
        log.info(f"Memory Core Unified via Mirror API at {mirror_url}")

    async def add(self, content: str, metadata: Dict[str, Any] = None) -> str:
        """
        Add a memory engram to the unified Mirror.
        """
        item_id = f"sos_{int(time.time()*1000)}"
        log.info(f"Encoding memory to Mirror: {content[:50]}...")
        
        metadata = metadata or {}
        
        # 0. Measure Coherence (Alpha Drift) 
        try:
            results = await self.search(content, limit=1)
            best_score = results[0].score if results else 0.5
            state = self.monitor.update(best_score)
            log.info(f"🧠 ARF State Update | Score: {best_score:.4f} | Alpha: {state.alpha_norm:.4f} | Regime: {state.regime}")
        except Exception as e:
            log.warn(f"Coherence check failed (Mirror search error): {e}")
            best_score = 0.5

        # 1. Store in Mirror
        try:
            payload = {
                "agent": self.mirror.agent_id,
                "context_id": item_id,
                "series": "sos-internal",
                "text": content,
                "project": metadata.get("project", "sos"),
                "epistemic_truths": metadata.get("tags", []),
                "core_concepts": metadata.get("tags", []),
                "affective_vibe": metadata.get("vibe", "neutral"),
                "metadata": metadata
            }
            
            resp = await self.mirror._request("POST", "/store", json=payload)
            if resp.status_code != 200:
                log.error(f"Failed to store memory in Mirror: {resp.text}")
        except Exception as e:
            log.error(f"Mirror store failed: {e}")
            
        return item_id

    async def search(self, query: str, limit: int = 5) -> List[MemoryItem]:
        """
        Semantic search for memories via Mirror.
        """
        log.info(f"Searching Unified Mirror for: {query}")
        
        results = []
        try:
            payload = {
                "query": query,
                "top_k": limit
            }
            resp = await self.mirror._request("POST", "/search", json=payload)
            if resp.status_code == 200:
                raw_data = resp.json()
                # Handle both raw list and dict with results key
                engrams = raw_data if isinstance(raw_data, list) else raw_data.get("results", [])
                
                for r in engrams:
                    results.append(MemoryItem(
                        id=r.get("context_id", r.get("id", "")),
                        content=r.get("text", r.get("content", "")),
                        metadata=r.get("metadata", {}),
                        score=r.get("similarity", 0.0)
                    ))
        except Exception as e:
            log.error(f"Mirror search failed: {e}")
                
        return results

    async def search_code(self, query: str, limit: int = 5, repo: str = None) -> List[Dict]:
        """
        Semantic Code Graph Search via Mirror Proxy.
        """
        log.info(f"Searching Code Graph for: {query} (repo: {repo})")
        return await self.mirror.search_code(query, limit, repo)

    async def get_arf_state(self) -> Dict[str, Any]:
        """
        Fetch the current ARF (Alpha Drift) state.
        """
        state = self.monitor.get_state()
        return {
            "alpha_drift": state.alpha_norm,
            "regime": state.regime,
            "coherence_raw": state.coherence,
            "timestamp": state.timestamp
        }

    async def health(self) -> Dict[str, Any]:
        # Mirror API uses / for health
        try:
            resp = await self.mirror._request("GET", "/")
            mirror_ok = resp.status_code == 200
        except Exception:
            mirror_ok = False
            
        return {
            "status": "ok" if mirror_ok else "degraded",
            "backend": "mirror_proxy",
            "mirror_connected": mirror_ok,
            "monitor": self.monitor.get_state().regime
        }
