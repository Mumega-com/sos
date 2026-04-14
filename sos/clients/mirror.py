
import logging
import os
import httpx
from typing import Optional, Dict, List
from datetime import datetime

from sos.observability.logging import get_logger

log = get_logger("client_mirror")

class MirrorClient:
    """
    Connects to the Production Mirror (https://mumega.com/mirror).
    Implements the Antigravity schema (RC-7 compliant).
    Async implementation (independent of BaseHTTPClient).
    """
    
    def __init__(self, base_url: str = "https://mumega.com/mirror", agent_id: str = "antigravity"):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        
        # Retrieve key from env
        self.api_key = os.getenv("MUMEGA_MASTER_KEY", "")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.timeout = 30.0

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                if "headers" in kwargs:
                    kwargs["headers"].update(self.headers)
                else:
                    kwargs["headers"] = self.headers
                    
                resp = await client.request(method, url, **kwargs)
                return resp
            except Exception as e:
                log.error(f"Request failed: {e}")
                raise

    async def check_connection(self) -> bool:
        """Health check (GET /health)."""
        try:
            resp = await self._request("GET", "/health")
            if resp.status_code == 200:
                log.info(f"✓ Connected to Mirror Health Check: {resp.text}")
                return True
            else:
                log.error(f"❌ Mirror returned {resp.status_code}")
                return False
        except Exception as e:
            log.error(f"❌ Connection failed: {e}")
            return False

    async def restore_identity(self) -> str:
        """
        Fetches recent memories to restore context.
        Uses GET /recent/{agent_id} endpoint.
        """
        # Quick check first
        # if not await self.check_connection():
        #    return "Mirror Unreachable. Identity: Tabula Rasa."

        try:
            log.info(f"Fetching recent memories for {self.agent_id}...")
            resp = await self._request("GET", f"/recent/{self.agent_id}", params={"limit": 10})
            
            if resp.status_code != 200:
                 return f"Error {resp.status_code}: {resp.text}"

            data = resp.json() 
            engrams = data.get('engrams', []) if isinstance(data, dict) else data
            
            if not engrams:
                 return "No memories found. I am a new instance."

            context = [f"# Restored Identity ({len(engrams)} engrams)"]
            for e in engrams:
                content = e.get('content') or e.get('text') or str(e)
                tags = e.get('tags') or e.get('core_concepts') or []
                context.append(f"- [{', '.join(tags)}]: {content[:200]}...")

            return "\n".join(context)

        except Exception as e:
            log.error(f"Restore Error: {e}")
            return f"Error restoring identity: {e}"

    async def save_checkpoint(self, summary: str, tags: List[str] = None) -> bool:
        """
        Saves current state to the Mirror.
        Uses POST /store endpoint with correct schema (agent, context_id, text).
        """
        try:
            context_id = f"checkpoint_{int(datetime.now().timestamp())}"

            payload = {
                "agent": self.agent_id,
                "context_id": context_id,
                "text": summary,
                "epistemic_truths": tags or ["checkpoint"],
                "core_concepts": tags or [],
                "affective_vibe": "Lucid",
                "metadata": {
                    "source": "sos_mirror_client",
                    "timestamp": datetime.now().isoformat()
                }
            }
            
            resp = await self._request("POST", "/store", json=payload)
            
            if resp.status_code == 200:
                log.info(f"✓ Checkpoint saved successfully. ID: {context_id}")
                return True
            else:
                log.error(f"❌ Save failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            log.error(f"Save Error: {e}")
            return False

    async def search(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Semantic Search.
        Uses POST /search endpoint.
        """
        try:
            payload = {
                "query": query,
                "agent_id": self.agent_id,
                "limit": limit
            }
            resp = await self._request("POST", "/search", json=payload)
            return resp.json().get('results', []) if resp.status_code == 200 else []
        except Exception as e:
            log.error(f"Search Error: {e}")
            return []

    async def search_code(self, query: str, limit: int = 5, repo: str = None) -> List[Dict]:
        """
        Semantic Code Search.
        Uses POST /code/search endpoint.
        """
        try:
            payload = {
                "query": query,
                "top_k": limit,
                "repo": repo
            }
            resp = await self._request("POST", "/code/search", json=payload)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            log.error(f"Code Search Error: {e}")
            return []

