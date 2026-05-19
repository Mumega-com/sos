"""
Cloudflare Storage Vendor for SOS.

Implements the StorageVendor contract using Cloudflare services:
- KV: Cloudflare Workers KV
- Object: Cloudflare R2
- SQL: Cloudflare D1
- Vector: Cloudflare Vectorize

All operations go through the MCP Gateway for unified access.
"""

import os
import json
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Union, BinaryIO
from dataclasses import dataclass
from io import BytesIO

import httpx

from sos.contracts.storage import (
    StorageVendor,
    StorageType,
    StorageError,
    StorageErrorCode,
    KVStore,
    KVEntry,
    ObjectStore,
    ObjectMeta,
    SQLStore,
    SQLResult,
    VectorStore,
    VectorEntry,
    VectorMatch,
    register_vendor,
)
from sos.observability.logging import get_logger

log = get_logger("cloudflare_vendor")


@dataclass
class CloudflareConfig:
    """Configuration for Cloudflare storage vendor."""
    gateway_url: str = "https://gateway.mumega.com/"
    timeout_seconds: float = 30.0
    api_token: Optional[str] = None
    account_id: Optional[str] = None
    # Namespace/binding names
    kv_namespace: str = "SOS_KV"
    r2_bucket: str = "sos-storage"
    d1_database: str = "sos-db"
    vectorize_index: str = "sos-vectors"
    vector_dimension: int = 1536  # OpenAI ada-002 dimension


class GatewayClient:
    """HTTP client for MCP Gateway communication."""

    def __init__(self, config: CloudflareConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "SOS-CloudflareVendor/1.0",
                },
            )

    async def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make a request to the Gateway.

        Args:
            action: Gateway action name
            payload: Request payload

        Returns:
            Response result dict

        Raises:
            StorageError on failure
        """
        if self._client is None:
            await self.connect()

        try:
            response = await self._client.post(
                self.config.gateway_url,
                json={"action": action, "payload": payload or {}},
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("success", True):
                error_msg = data.get("error", "Gateway request failed")
                raise StorageError(
                    StorageErrorCode.INTERNAL_ERROR,
                    error_msg,
                    {"action": action},
                )

            return data.get("result", data)

        except httpx.TimeoutException:
            raise StorageError(
                StorageErrorCode.TIMEOUT,
                f"Gateway timeout for action: {action}",
                {"action": action},
            )
        except httpx.HTTPStatusError as e:
            code = StorageErrorCode.INTERNAL_ERROR
            if e.response.status_code == 404:
                code = StorageErrorCode.NOT_FOUND
            elif e.response.status_code == 403:
                code = StorageErrorCode.PERMISSION_DENIED
            raise StorageError(
                code,
                f"Gateway HTTP error: {e.response.status_code}",
                {"action": action, "status": e.response.status_code},
            )
        except Exception as e:
            raise StorageError(
                StorageErrorCode.CONNECTION_ERROR,
                f"Gateway error: {str(e)}",
                {"action": action},
            )


class CloudflareKVStore(KVStore):
    """Cloudflare Workers KV implementation."""

    def __init__(self, client: GatewayClient, namespace: str):
        self.client = client
        self.namespace = namespace

    async def get(self, key: str) -> Optional[Any]:
        """Get value from KV."""
        try:
            result = await self.client.request("kv_get", {
                "namespace": self.namespace,
                "key": key,
            })
            return result.get("value")
        except StorageError as e:
            if e.code == StorageErrorCode.NOT_FOUND:
                return None
            raise

    async def get_with_metadata(self, key: str) -> Optional[KVEntry]:
        """Get value with metadata from KV."""
        try:
            result = await self.client.request("kv_get_with_metadata", {
                "namespace": self.namespace,
                "key": key,
            })
            if result.get("value") is None:
                return None
            return KVEntry(
                key=key,
                value=result.get("value"),
                metadata=result.get("metadata", {}),
                expiration=None,  # KV doesn't return expiration
            )
        except StorageError as e:
            if e.code == StorageErrorCode.NOT_FOUND:
                return None
            raise

    async def put(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store value in KV."""
        payload = {
            "namespace": self.namespace,
            "key": key,
            "value": value,
        }
        if ttl_seconds:
            payload["expirationTtl"] = ttl_seconds
        if metadata:
            payload["metadata"] = metadata

        await self.client.request("kv_put", payload)
        return True

    async def delete(self, key: str) -> bool:
        """Delete key from KV."""
        try:
            await self.client.request("kv_delete", {
                "namespace": self.namespace,
                "key": key,
            })
            return True
        except StorageError as e:
            if e.code == StorageErrorCode.NOT_FOUND:
                return False
            raise

    async def list(
        self,
        prefix: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[List[str], Optional[str]]:
        """List keys in KV."""
        payload = {
            "namespace": self.namespace,
            "limit": limit,
        }
        if prefix:
            payload["prefix"] = prefix
        if cursor:
            payload["cursor"] = cursor

        result = await self.client.request("kv_list", payload)
        keys = [k.get("name", k) if isinstance(k, dict) else k for k in result.get("keys", [])]
        return keys, result.get("cursor")

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        value = await self.get(key)
        return value is not None


class CloudflareObjectStore(ObjectStore):
    """Cloudflare R2 implementation."""

    def __init__(self, client: GatewayClient, bucket: str):
        self.client = client
        self.bucket = bucket

    async def get(self, key: str) -> Optional[bytes]:
        """Get object content."""
        try:
            result = await self.client.request("r2_get", {
                "bucket": self.bucket,
                "key": key,
            })
            # Gateway returns base64-encoded content for binary
            content = result.get("body")
            if content is None:
                return None
            if isinstance(content, str):
                import base64
                return base64.b64decode(content)
            return content
        except StorageError as e:
            if e.code == StorageErrorCode.NOT_FOUND:
                return None
            raise

    async def get_stream(self, key: str) -> Optional[BinaryIO]:
        """Get object as stream."""
        content = await self.get(key)
        if content is None:
            return None
        return BytesIO(content)

    async def head(self, key: str) -> Optional[ObjectMeta]:
        """Get object metadata."""
        try:
            result = await self.client.request("r2_head", {
                "bucket": self.bucket,
                "key": key,
            })
            return ObjectMeta(
                key=key,
                size=result.get("size", 0),
                content_type=result.get("httpMetadata", {}).get("contentType", "application/octet-stream"),
                etag=result.get("etag"),
                last_modified=datetime.fromisoformat(result["uploaded"]) if result.get("uploaded") else None,
                metadata=result.get("customMetadata", {}),
            )
        except StorageError as e:
            if e.code == StorageErrorCode.NOT_FOUND:
                return None
            raise

    async def put(
        self,
        key: str,
        data: Union[bytes, BinaryIO],
        content_type: str = "application/octet-stream",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ObjectMeta:
        """Store object in R2."""
        import base64

        if hasattr(data, "read"):
            content = data.read()
        else:
            content = data

        payload = {
            "bucket": self.bucket,
            "key": key,
            "body": base64.b64encode(content).decode("utf-8"),
            "httpMetadata": {"contentType": content_type},
        }
        if metadata:
            payload["customMetadata"] = metadata

        result = await self.client.request("r2_put", payload)
        return ObjectMeta(
            key=key,
            size=len(content),
            content_type=content_type,
            etag=result.get("etag"),
            metadata=metadata or {},
        )

    async def delete(self, key: str) -> bool:
        """Delete object from R2."""
        try:
            await self.client.request("r2_delete", {
                "bucket": self.bucket,
                "key": key,
            })
            return True
        except StorageError as e:
            if e.code == StorageErrorCode.NOT_FOUND:
                return False
            raise

    async def list(
        self,
        prefix: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[List[ObjectMeta], Optional[str]]:
        """List objects in R2."""
        payload = {
            "bucket": self.bucket,
            "limit": limit,
        }
        if prefix:
            payload["prefix"] = prefix
        if cursor:
            payload["cursor"] = cursor

        result = await self.client.request("r2_list", payload)
        objects = []
        for obj in result.get("objects", []):
            objects.append(ObjectMeta(
                key=obj.get("key"),
                size=obj.get("size", 0),
                content_type=obj.get("httpMetadata", {}).get("contentType", "application/octet-stream"),
                etag=obj.get("etag"),
                last_modified=datetime.fromisoformat(obj["uploaded"]) if obj.get("uploaded") else None,
                metadata=obj.get("customMetadata", {}),
            ))
        return objects, result.get("cursor")

    async def copy(self, source_key: str, dest_key: str) -> ObjectMeta:
        """Copy object within R2."""
        result = await self.client.request("r2_copy", {
            "bucket": self.bucket,
            "sourceKey": source_key,
            "destKey": dest_key,
        })
        return ObjectMeta(
            key=dest_key,
            size=result.get("size", 0),
            content_type=result.get("httpMetadata", {}).get("contentType", "application/octet-stream"),
            etag=result.get("etag"),
            metadata=result.get("customMetadata", {}),
        )


class CloudflareSQLStore(SQLStore):
    """Cloudflare D1 implementation."""

    def __init__(self, client: GatewayClient, database: str):
        self.client = client
        self.database = database

    async def execute(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> SQLResult:
        """Execute SQL statement."""
        result = await self.client.request("d1_execute", {
            "database": self.database,
            "sql": sql,
            "params": params or [],
        })
        return SQLResult(
            rows=result.get("results", []),
            columns=result.get("columns", []),
            rows_affected=result.get("changes", 0),
            last_insert_id=result.get("last_row_id"),
        )

    async def execute_batch(
        self,
        statements: List[tuple[str, Optional[List[Any]]]],
    ) -> List[SQLResult]:
        """Execute batch of SQL statements."""
        batch = [
            {"sql": sql, "params": params or []}
            for sql, params in statements
        ]
        results = await self.client.request("d1_batch", {
            "database": self.database,
            "statements": batch,
        })
        return [
            SQLResult(
                rows=r.get("results", []),
                columns=r.get("columns", []),
                rows_affected=r.get("changes", 0),
                last_insert_id=r.get("last_row_id"),
            )
            for r in results.get("results", [])
        ]

    async def query_one(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Query single row."""
        result = await self.execute(sql, params)
        return result.rows[0] if result.rows else None

    async def query_all(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Query all rows."""
        result = await self.execute(sql, params)
        return result.rows

    async def table_exists(self, table_name: str) -> bool:
        """Check if table exists in D1."""
        result = await self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            [table_name],
        )
        return result is not None


class CloudflareVectorStore(VectorStore):
    """Cloudflare Vectorize implementation."""

    def __init__(self, client: GatewayClient, index: str, dimension: int = 1536):
        self.client = client
        self.index = index
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        """Vector dimension."""
        return self._dimension

    async def upsert(self, vectors: List[VectorEntry]) -> int:
        """Upsert vectors to Vectorize."""
        # Format vectors for Vectorize API
        formatted = [
            {
                "id": v.id,
                "values": v.vector,
                "metadata": v.metadata,
                "namespace": v.namespace,
            }
            for v in vectors
        ]
        result = await self.client.request("vectorize_upsert", {
            "index": self.index,
            "vectors": formatted,
        })
        return result.get("count", len(vectors))

    async def query(
        self,
        vector: List[float],
        top_k: int = 10,
        namespace: str = "default",
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[VectorMatch]:
        """Query similar vectors."""
        payload = {
            "index": self.index,
            "vector": vector,
            "topK": top_k,
            "namespace": namespace,
        }
        if filter:
            payload["filter"] = filter

        result = await self.client.request("vectorize_query", payload)
        return [
            VectorMatch(
                id=m.get("id"),
                score=m.get("score", 0.0),
                metadata=m.get("metadata", {}),
            )
            for m in result.get("matches", [])
        ]

    async def delete(
        self,
        ids: List[str],
        namespace: str = "default",
    ) -> int:
        """Delete vectors by ID."""
        result = await self.client.request("vectorize_delete", {
            "index": self.index,
            "ids": ids,
            "namespace": namespace,
        })
        return result.get("count", 0)

    async def delete_namespace(self, namespace: str) -> int:
        """Delete all vectors in namespace."""
        result = await self.client.request("vectorize_delete_namespace", {
            "index": self.index,
            "namespace": namespace,
        })
        return result.get("count", 0)

    async def count(self, namespace: str = "default") -> int:
        """Count vectors in namespace."""
        result = await self.client.request("vectorize_describe", {
            "index": self.index,
            "namespace": namespace,
        })
        return result.get("vectorCount", 0)


class CloudflareStorageVendor(StorageVendor):
    """
    Cloudflare Storage Vendor implementation.

    Provides access to Cloudflare Workers KV, R2, D1, and Vectorize
    through the MCP Gateway.
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        config: Optional[CloudflareConfig] = None,
        **kwargs,
    ):
        """
        Initialize Cloudflare storage vendor.

        Args:
            gateway_url: Gateway URL (overrides config)
            config: Full configuration object
            **kwargs: Additional config overrides
        """
        self.config = config or CloudflareConfig()
        if gateway_url:
            self.config.gateway_url = gateway_url
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

        self._client = GatewayClient(self.config)
        self._connected = False

        # Store caches
        self._kv_stores: Dict[str, CloudflareKVStore] = {}
        self._object_stores: Dict[str, CloudflareObjectStore] = {}
        self._sql_stores: Dict[str, CloudflareSQLStore] = {}
        self._vector_stores: Dict[str, CloudflareVectorStore] = {}

    @property
    def name(self) -> str:
        return "cloudflare"

    @property
    def available_types(self) -> List[StorageType]:
        return [
            StorageType.KV,
            StorageType.OBJECT,
            StorageType.SQL,
            StorageType.VECTOR,
        ]

    async def connect(self) -> bool:
        """Connect to Cloudflare via Gateway."""
        await self._client.connect()
        self._connected = True
        log.info("Connected to Cloudflare storage via Gateway", url=self.config.gateway_url)
        return True

    async def disconnect(self) -> None:
        """Disconnect from Gateway."""
        await self._client.disconnect()
        self._connected = False
        log.info("Disconnected from Cloudflare storage")

    async def health(self) -> Dict[str, Any]:
        """Check storage health."""
        try:
            result = await self._client.request("health", {})
            return {
                "healthy": True,
                "vendor": "cloudflare",
                "gateway": self.config.gateway_url,
                "details": result,
            }
        except StorageError as e:
            return {
                "healthy": False,
                "vendor": "cloudflare",
                "gateway": self.config.gateway_url,
                "error": str(e),
            }

    def kv(self, namespace: str = "default") -> KVStore:
        """Get KV store for namespace."""
        if namespace not in self._kv_stores:
            ns = f"{self.config.kv_namespace}:{namespace}"
            self._kv_stores[namespace] = CloudflareKVStore(self._client, ns)
        return self._kv_stores[namespace]

    def objects(self, bucket: str = "default") -> ObjectStore:
        """Get Object store for bucket."""
        if bucket not in self._object_stores:
            b = bucket if bucket != "default" else self.config.r2_bucket
            self._object_stores[bucket] = CloudflareObjectStore(self._client, b)
        return self._object_stores[bucket]

    def sql(self, database: str = "default") -> SQLStore:
        """Get SQL store for database."""
        if database not in self._sql_stores:
            db = database if database != "default" else self.config.d1_database
            self._sql_stores[database] = CloudflareSQLStore(self._client, db)
        return self._sql_stores[database]

    def vectors(self, index: str = "default") -> VectorStore:
        """Get Vector store for index."""
        if index not in self._vector_stores:
            idx = index if index != "default" else self.config.vectorize_index
            self._vector_stores[index] = CloudflareVectorStore(
                self._client,
                idx,
                self.config.vector_dimension,
            )
        return self._vector_stores[index]


# Register vendor
register_vendor("cloudflare", CloudflareStorageVendor)


# Factory function for convenience
def create_cloudflare_vendor(
    gateway_url: Optional[str] = None,
    **config,
) -> CloudflareStorageVendor:
    """
    Create a Cloudflare storage vendor.

    Args:
        gateway_url: Gateway URL (default: env GATEWAY_URL or https://gateway.mumega.com/)
        **config: Additional configuration

    Returns:
        CloudflareStorageVendor instance
    """
    url = gateway_url or os.getenv("GATEWAY_URL", "https://gateway.mumega.com/")
    return CloudflareStorageVendor(gateway_url=url, **config)
