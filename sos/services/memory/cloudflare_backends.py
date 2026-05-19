"""
Cloudflare Backends for SOS Memory Service
==========================================

Provides D1 (metadata) and Vectorize (embeddings) backends that connect
to the MCP Gateway at gateway.mumega.com.

Usage:
    Set environment variables:
        SOS_MEMORY_BACKEND=cloudflare
        GATEWAY_URL=https://gateway.mumega.com/

    Then MemoryCore will use these backends instead of local SQLite/ChromaDB.
"""

import os
import time
import json
import httpx
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from sos.observability.logging import get_logger
from sos.services.memory.backends import MetadataStore, MemoryMetadata
from sos.services.memory.vector_store import VectorItem

log = get_logger("cloudflare_backends")

# Gateway configuration
GATEWAY_URL = os.getenv("GATEWAY_URL", "https://gateway.mumega.com/")
GATEWAY_TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT", "30"))


def _gateway_request(action: str, payload: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Synchronous request to Gateway.
    Used by backends that need sync interface.
    """
    try:
        response = httpx.post(
            GATEWAY_URL,
            json={"action": action, "payload": payload or {}},
            timeout=GATEWAY_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            raise Exception(data.get("error", "Gateway request failed"))

        return data.get("result", {})
    except httpx.HTTPError as e:
        log.error("Gateway HTTP error", action=action, error=str(e))
        raise
    except Exception as e:
        log.error("Gateway request failed", action=action, error=str(e))
        raise


class CloudflareD1MetadataStore(MetadataStore):
    """
    Cloudflare D1 implementation for metadata storage.
    Uses the MCP Gateway to store engrams in D1.
    """

    def __init__(self, agent: str = "sos"):
        """
        Initialize D1 metadata store.

        Args:
            agent: Agent namespace for storing memories (default: "sos")
        """
        self.agent = agent
        log.info(f"Initialized CloudflareD1MetadataStore for agent: {agent}")

    def add(self, item_id: str, metadata: Dict[str, Any]) -> None:
        """Store metadata in D1 via Gateway."""
        try:
            # Use memory_store action which stores in D1 engrams table
            _gateway_request("memory_store", {
                "agent": self.agent,
                "text": metadata.get("content", json.dumps(metadata)),
                "context_id": item_id,
                "metadata": {
                    "source": metadata.get("source", "sos"),
                    "type": metadata.get("type", "memory"),
                    "tags": metadata.get("tags", []),
                    "timestamp": time.time(),
                    "raw": metadata
                }
            })
            log.debug(f"Stored metadata in D1: {item_id}")
        except Exception as e:
            log.error("D1 add failed", item_id=item_id, error=str(e))
            raise

    def get(self, item_id: str) -> Optional[MemoryMetadata]:
        """Get metadata from D1 via Gateway search."""
        try:
            # Search for specific context_id
            result = _gateway_request("memory_search", {
                "agent": self.agent,
                "query": item_id,
                "limit": 1
            })

            results = result.get("results", [])
            if results:
                r = results[0]
                metadata = r.get("metadata", {})
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)

                return MemoryMetadata(
                    id=r.get("id", item_id),
                    timestamp=metadata.get("timestamp", time.time()),
                    source=metadata.get("source", "unknown"),
                    type=metadata.get("type", "memory"),
                    tags=metadata.get("tags", []),
                    raw_metadata=metadata.get("raw", metadata)
                )
        except Exception as e:
            log.error("D1 get failed", item_id=item_id, error=str(e))
        return None

    def list(self, limit: int = 10) -> List[MemoryMetadata]:
        """List recent metadata from D1."""
        results = []
        try:
            result = _gateway_request("memory_list", {
                "agent": self.agent,
                "limit": limit
            })

            for engram in result.get("engrams", []):
                metadata = engram.get("metadata", {})
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)

                results.append(MemoryMetadata(
                    id=engram.get("id"),
                    timestamp=metadata.get("timestamp", 0),
                    source=metadata.get("source", "unknown"),
                    type=metadata.get("type", "memory"),
                    tags=metadata.get("tags", []),
                    raw_metadata=metadata
                ))
        except Exception as e:
            log.error("D1 list failed", error=str(e))
        return results


class CloudflareVectorizeBackend:
    """
    Cloudflare Vectorize implementation for vector storage.
    Uses the MCP Gateway for semantic search.

    Note: Embedding generation happens at the Gateway (OpenAI API).
    """

    def __init__(self, agent: str = "sos"):
        """
        Initialize Vectorize backend.

        Args:
            agent: Agent namespace for vector storage
        """
        self.agent = agent
        self._count = 0
        log.info(f"Initialized CloudflareVectorizeBackend for agent: {agent}")

    def add(self, item_id: str, content: str, metadata: Dict[str, Any]) -> None:
        """
        Store content with vector embedding in Vectorize.
        The Gateway handles embedding generation via OpenAI.
        """
        try:
            _gateway_request("memory_store", {
                "agent": self.agent,
                "text": content,
                "context_id": item_id,
                "metadata": metadata
            })
            self._count += 1
            log.debug(f"Stored vector in Vectorize: {item_id}")
        except Exception as e:
            log.error("Vectorize add failed", item_id=item_id, error=str(e))
            # Don't raise - allow degraded operation

    def search(self, query: str, limit: int = 5) -> List[VectorItem]:
        """
        Semantic search via Gateway.
        Gateway uses Vectorize for vector similarity, falls back to text search.

        For agent="river", uses river_search which searches river_* tables.
        For other agents, uses memory_search on engrams table.
        """
        try:
            # River has special tables (river_dreams, river_conversations, etc.)
            if self.agent == "river":
                result = _gateway_request("river_search", {
                    "query": query,
                    "limit": limit,
                    "table": "all"
                })
                items = []
                for r in result.get("results", []):
                    items.append(VectorItem(
                        id=str(r.get("id", "")),
                        content=r.get("content", r.get("preview", "")),
                        metadata={"table": r.get("table", ""), "timestamp": r.get("timestamp", "")},
                        score=r.get("score", 0.5)  # Text search doesn't return scores
                    ))
                return items
            else:
                # Standard agent search via engrams table
                result = _gateway_request("memory_search", {
                    "agent": self.agent,
                    "query": query,
                    "limit": limit
                })
                items = []
                for r in result.get("results", []):
                    items.append(VectorItem(
                        id=r.get("id", ""),
                        content=r.get("text", ""),
                        metadata=r.get("metadata", {}),
                        score=r.get("score", 0.0)
                    ))
                return items
        except Exception as e:
            log.error("Vectorize search failed", query=query[:50], error=str(e))
            return []

    def count(self) -> int:
        """Return approximate count of stored vectors."""
        # Could query Gateway for exact count, but this is faster
        return self._count


class CloudflareKVCache:
    """
    Cloudflare KV for session caching.
    Provides Redis-like operations via Gateway.
    """

    def __init__(self, prefix: str = "sos"):
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set a value in KV."""
        try:
            payload = {"key": self._key(key), "value": value}
            if ttl:
                payload["ttl"] = ttl
            _gateway_request("session_set", payload)
            return True
        except Exception as e:
            log.error("KV set failed", key=key, error=str(e))
            return False

    def get(self, key: str) -> Optional[Any]:
        """Get a value from KV."""
        try:
            result = _gateway_request("session_get", {"key": self._key(key)})
            return result.get("value")
        except Exception as e:
            log.error("KV get failed", key=key, error=str(e))
            return None

    def push(self, key: str, value: Any, max_items: int = 100) -> bool:
        """Push to a list in KV."""
        try:
            _gateway_request("session_push", {
                "key": self._key(key),
                "value": value,
                "max": max_items
            })
            return True
        except Exception as e:
            log.error("KV push failed", key=key, error=str(e))
            return False

    def list(self, key: str, limit: int = 10) -> List[Any]:
        """Get list items from KV."""
        try:
            result = _gateway_request("session_list", {
                "key": self._key(key),
                "limit": limit
            })
            return result.get("items", [])
        except Exception as e:
            log.error("KV list failed", key=key, error=str(e))
            return []

    def delete(self, key: str) -> bool:
        """Delete a key from KV."""
        try:
            _gateway_request("session_delete", {"key": self._key(key)})
            return True
        except Exception as e:
            log.error("KV delete failed", key=key, error=str(e))
            return False


# Factory function for easy backend creation
def create_cloudflare_backends(agent: str = "sos") -> tuple:
    """
    Create Cloudflare backends for SOS.

    Returns:
        Tuple of (metadata_store, vector_store, kv_cache)
    """
    return (
        CloudflareD1MetadataStore(agent=agent),
        CloudflareVectorizeBackend(agent=agent),
        CloudflareKVCache(prefix=agent)
    )
