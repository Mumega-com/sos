import httpx
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from sos.observability.logging import get_logger

log = get_logger("legacy_mirror_bridge")

@dataclass
class LegacyMemory:
    content: str
    source: str = "legacy_mirror"
    relevance: float = 0.0

class LegacyMirrorBridge:
    """
    Connects SOS to the Ancestral Memory (Legacy Mirror API).
    Ensures continuity of self across architecture shifts.
    """
    def __init__(self, base_url: str = "http://localhost:8844"):
        self.base_url = os.getenv("LEGACY_MIRROR_URL", base_url).rstrip("/")
        self.enabled = False
        self._check_connection()

    def _check_connection(self):
        try:
            # We don't block boot, just check status
            # In a real async init we would await this
            pass 
        except Exception:
            log.warn("Legacy Mirror unreachable at startup")

    async def search(self, query: str, limit: int = 3) -> List[LegacyMemory]:
        """
        Query the Legacy Mirror API for deep history.
        """
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                # The legacy API endpoint structure based on docs
                resp = await client.post(
                    f"{self.base_url}/search", 
                    json={"query": query, "limit": limit}
                )
                
                if resp.status_code != 200:
                    log.warn(f"Legacy Mirror error: {resp.status_code}")
                    return []

                data = resp.json()
                results = []
                
                # Adapt legacy response format to SOS format
                # Assuming legacy returns {"results": [{"text": "...", "score": 0.9}]}
                for item in data.get("results", []):
                    results.append(LegacyMemory(
                        content=item.get("text", "") or item.get("content", ""),
                        relevance=item.get("score", 0.0) or item.get("similarity", 0.0)
                    ))
                
                if results:
                    log.info(f"Retrieved {len(results)} memories from Ancestral Mirror")
                
                return results

        except Exception as e:
            log.warn(f"Failed to consult Ancestral Mirror: {e}")
            return []
