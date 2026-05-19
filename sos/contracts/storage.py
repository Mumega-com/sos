"""
SOS Storage Vendor Contract.

The Storage Contract defines a unified interface for storage backends:
- Key-Value storage (KV)
- Object/Blob storage (R2-like)
- SQL database (D1-like)
- Vector store (Vectorize-like)

Vendors implement this contract to provide storage for SOS services.
Examples: Cloudflare (KV, R2, D1, Vectorize), Local (SQLite, filesystem, ChromaDB)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union, BinaryIO
from enum import Enum


class StorageType(Enum):
    """Types of storage backends."""
    KV = "kv"           # Key-value store
    OBJECT = "object"   # Object/blob storage
    SQL = "sql"         # SQL database
    VECTOR = "vector"   # Vector store


class StorageErrorCode(Enum):
    """Standard storage error codes."""
    NOT_FOUND = 4040
    ALREADY_EXISTS = 4090
    QUOTA_EXCEEDED = 4290
    INVALID_KEY = 4000
    INVALID_VALUE = 4001
    CONNECTION_ERROR = 5000
    TIMEOUT = 5040
    PERMISSION_DENIED = 4030
    INTERNAL_ERROR = 5001


class StorageError(Exception):
    """Base exception for storage errors."""

    def __init__(
        self,
        code: StorageErrorCode,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class KVEntry:
    """A key-value entry."""
    key: str
    value: Any
    metadata: Dict[str, Any] = field(default_factory=dict)
    expiration: Optional[datetime] = None


@dataclass
class ObjectMeta:
    """Metadata for a stored object."""
    key: str
    size: int
    content_type: str
    etag: Optional[str] = None
    last_modified: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorEntry:
    """A vector entry with embedding."""
    id: str
    vector: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)
    namespace: str = "default"


@dataclass
class VectorMatch:
    """Result of vector similarity search."""
    id: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SQLResult:
    """Result of SQL query."""
    rows: List[Dict[str, Any]]
    columns: List[str]
    rows_affected: int = 0
    last_insert_id: Optional[int] = None


class KVStore(ABC):
    """
    Abstract Key-Value store interface.

    Implementations: Cloudflare KV, Redis, Local file-based, etc.
    """

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """
        Get a value by key.

        Args:
            key: The key to retrieve

        Returns:
            The value if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_with_metadata(self, key: str) -> Optional[KVEntry]:
        """
        Get a value with its metadata.

        Args:
            key: The key to retrieve

        Returns:
            KVEntry if found, None otherwise
        """
        pass

    @abstractmethod
    async def put(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Store a value.

        Args:
            key: The key to store
            value: The value to store (will be JSON serialized)
            ttl_seconds: Optional time-to-live in seconds
            metadata: Optional metadata

        Returns:
            True if stored successfully
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        Delete a key.

        Args:
            key: The key to delete

        Returns:
            True if deleted, False if not found
        """
        pass

    @abstractmethod
    async def list(
        self,
        prefix: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[List[str], Optional[str]]:
        """
        List keys with optional prefix filtering.

        Args:
            prefix: Optional key prefix filter
            limit: Maximum keys to return
            cursor: Pagination cursor

        Returns:
            Tuple of (keys, next_cursor)
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        pass


class ObjectStore(ABC):
    """
    Abstract Object/Blob store interface.

    Implementations: Cloudflare R2, S3, Local filesystem, etc.
    """

    @abstractmethod
    async def get(self, key: str) -> Optional[bytes]:
        """
        Get object content by key.

        Args:
            key: The object key

        Returns:
            Object content as bytes, None if not found
        """
        pass

    @abstractmethod
    async def get_stream(self, key: str) -> Optional[BinaryIO]:
        """
        Get object as a stream.

        Args:
            key: The object key

        Returns:
            Binary stream, None if not found
        """
        pass

    @abstractmethod
    async def head(self, key: str) -> Optional[ObjectMeta]:
        """
        Get object metadata without content.

        Args:
            key: The object key

        Returns:
            ObjectMeta if found, None otherwise
        """
        pass

    @abstractmethod
    async def put(
        self,
        key: str,
        data: Union[bytes, BinaryIO],
        content_type: str = "application/octet-stream",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ObjectMeta:
        """
        Store an object.

        Args:
            key: The object key
            data: Content as bytes or stream
            content_type: MIME type
            metadata: Optional metadata

        Returns:
            ObjectMeta of stored object
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete an object."""
        pass

    @abstractmethod
    async def list(
        self,
        prefix: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[List[ObjectMeta], Optional[str]]:
        """
        List objects with optional prefix filtering.

        Args:
            prefix: Optional key prefix filter
            limit: Maximum objects to return
            cursor: Pagination cursor

        Returns:
            Tuple of (object_metas, next_cursor)
        """
        pass

    @abstractmethod
    async def copy(self, source_key: str, dest_key: str) -> ObjectMeta:
        """Copy an object to a new key."""
        pass


class SQLStore(ABC):
    """
    Abstract SQL database interface.

    Implementations: Cloudflare D1, SQLite, PostgreSQL, etc.
    """

    @abstractmethod
    async def execute(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> SQLResult:
        """
        Execute a SQL statement.

        Args:
            sql: SQL statement with ? placeholders
            params: Parameter values

        Returns:
            SQLResult with rows and metadata
        """
        pass

    @abstractmethod
    async def execute_batch(
        self,
        statements: List[tuple[str, Optional[List[Any]]]],
    ) -> List[SQLResult]:
        """
        Execute multiple SQL statements in a batch.

        Args:
            statements: List of (sql, params) tuples

        Returns:
            List of SQLResults
        """
        pass

    @abstractmethod
    async def query_one(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute query and return first row.

        Args:
            sql: SQL query
            params: Parameter values

        Returns:
            First row as dict, None if no results
        """
        pass

    @abstractmethod
    async def query_all(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute query and return all rows.

        Args:
            sql: SQL query
            params: Parameter values

        Returns:
            List of rows as dicts
        """
        pass

    @abstractmethod
    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        pass


class VectorStore(ABC):
    """
    Abstract Vector store interface.

    Implementations: Cloudflare Vectorize, ChromaDB, Pinecone, etc.
    """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector dimension for this store."""
        pass

    @abstractmethod
    async def upsert(
        self,
        vectors: List[VectorEntry],
    ) -> int:
        """
        Insert or update vectors.

        Args:
            vectors: List of VectorEntry to upsert

        Returns:
            Number of vectors upserted
        """
        pass

    @abstractmethod
    async def query(
        self,
        vector: List[float],
        top_k: int = 10,
        namespace: str = "default",
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[VectorMatch]:
        """
        Query for similar vectors.

        Args:
            vector: Query vector
            top_k: Number of results to return
            namespace: Namespace to search in
            filter: Optional metadata filter

        Returns:
            List of VectorMatch sorted by similarity
        """
        pass

    @abstractmethod
    async def delete(
        self,
        ids: List[str],
        namespace: str = "default",
    ) -> int:
        """
        Delete vectors by ID.

        Args:
            ids: Vector IDs to delete
            namespace: Namespace

        Returns:
            Number of vectors deleted
        """
        pass

    @abstractmethod
    async def delete_namespace(self, namespace: str) -> int:
        """Delete all vectors in a namespace."""
        pass

    @abstractmethod
    async def count(self, namespace: str = "default") -> int:
        """Count vectors in a namespace."""
        pass


class StorageVendor(ABC):
    """
    Abstract Storage Vendor interface.

    A vendor provides one or more storage backends (KV, Object, SQL, Vector).
    Implementations: CloudflareStorageVendor, LocalStorageVendor, etc.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Vendor name (e.g., 'cloudflare', 'local')."""
        pass

    @property
    @abstractmethod
    def available_types(self) -> List[StorageType]:
        """List of storage types this vendor provides."""
        pass

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to storage backend.

        Returns:
            True if connected successfully
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to storage backend."""
        pass

    @abstractmethod
    async def health(self) -> Dict[str, Any]:
        """
        Check storage health.

        Returns:
            Health status dict with 'healthy' bool and details
        """
        pass

    def kv(self, namespace: str = "default") -> KVStore:
        """
        Get KV store for a namespace.

        Args:
            namespace: KV namespace

        Returns:
            KVStore instance

        Raises:
            NotImplementedError if KV not available
        """
        raise NotImplementedError(f"{self.name} does not provide KV storage")

    def objects(self, bucket: str = "default") -> ObjectStore:
        """
        Get Object store for a bucket.

        Args:
            bucket: Object bucket name

        Returns:
            ObjectStore instance

        Raises:
            NotImplementedError if Object storage not available
        """
        raise NotImplementedError(f"{self.name} does not provide Object storage")

    def sql(self, database: str = "default") -> SQLStore:
        """
        Get SQL store for a database.

        Args:
            database: Database name

        Returns:
            SQLStore instance

        Raises:
            NotImplementedError if SQL not available
        """
        raise NotImplementedError(f"{self.name} does not provide SQL storage")

    def vectors(self, index: str = "default") -> VectorStore:
        """
        Get Vector store for an index.

        Args:
            index: Vector index name

        Returns:
            VectorStore instance

        Raises:
            NotImplementedError if Vector storage not available
        """
        raise NotImplementedError(f"{self.name} does not provide Vector storage")


# Vendor registry for dynamic discovery
_vendors: Dict[str, type] = {}


def register_vendor(name: str, vendor_class: type) -> None:
    """Register a storage vendor class."""
    _vendors[name] = vendor_class


def get_vendor(name: str, **config) -> StorageVendor:
    """
    Get a storage vendor by name.

    Args:
        name: Vendor name (e.g., 'cloudflare', 'local')
        **config: Vendor-specific configuration

    Returns:
        StorageVendor instance

    Raises:
        KeyError if vendor not registered
    """
    if name not in _vendors:
        raise KeyError(f"Unknown storage vendor: {name}. Available: {list(_vendors.keys())}")
    return _vendors[name](**config)


def list_vendors() -> List[str]:
    """List registered vendor names."""
    return list(_vendors.keys())
