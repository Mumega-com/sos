"""
Tests for Storage Contract and Cloudflare Vendor.

Tests the storage vendor abstraction and Cloudflare implementation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from io import BytesIO

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
    get_vendor,
    list_vendors,
)
from sos.vendors.cloudflare import (
    CloudflareStorageVendor,
    CloudflareConfig,
    CloudflareKVStore,
    CloudflareObjectStore,
    CloudflareSQLStore,
    CloudflareVectorStore,
    GatewayClient,
    create_cloudflare_vendor,
)


class TestStorageTypes:
    """Tests for storage type enums."""

    def test_storage_types(self):
        """Test all storage types exist."""
        assert StorageType.KV.value == "kv"
        assert StorageType.OBJECT.value == "object"
        assert StorageType.SQL.value == "sql"
        assert StorageType.VECTOR.value == "vector"

    def test_error_codes(self):
        """Test error codes."""
        assert StorageErrorCode.NOT_FOUND.value == 4040
        assert StorageErrorCode.TIMEOUT.value == 5040
        assert StorageErrorCode.PERMISSION_DENIED.value == 4030


class TestStorageError:
    """Tests for StorageError exception."""

    def test_create_error(self):
        """Test creating a storage error."""
        error = StorageError(
            StorageErrorCode.NOT_FOUND,
            "Key not found",
            {"key": "test"},
        )

        assert error.code == StorageErrorCode.NOT_FOUND
        assert error.message == "Key not found"
        assert error.details == {"key": "test"}

    def test_error_is_exception(self):
        """Test StorageError is an Exception."""
        error = StorageError(StorageErrorCode.TIMEOUT, "Timed out")

        with pytest.raises(StorageError) as exc_info:
            raise error

        assert exc_info.value.code == StorageErrorCode.TIMEOUT


class TestDataClasses:
    """Tests for storage data classes."""

    def test_kv_entry(self):
        """Test KVEntry dataclass."""
        entry = KVEntry(
            key="test-key",
            value={"data": "value"},
            metadata={"created": "2026-02-03"},
        )

        assert entry.key == "test-key"
        assert entry.value == {"data": "value"}
        assert entry.expiration is None

    def test_object_meta(self):
        """Test ObjectMeta dataclass."""
        meta = ObjectMeta(
            key="file.txt",
            size=1024,
            content_type="text/plain",
            etag="abc123",
        )

        assert meta.key == "file.txt"
        assert meta.size == 1024

    def test_vector_entry(self):
        """Test VectorEntry dataclass."""
        entry = VectorEntry(
            id="vec-1",
            vector=[0.1, 0.2, 0.3],
            metadata={"source": "test"},
            namespace="test-ns",
        )

        assert entry.id == "vec-1"
        assert len(entry.vector) == 3
        assert entry.namespace == "test-ns"

    def test_vector_match(self):
        """Test VectorMatch dataclass."""
        match = VectorMatch(
            id="vec-1",
            score=0.95,
            metadata={"label": "similar"},
        )

        assert match.score == 0.95

    def test_sql_result(self):
        """Test SQLResult dataclass."""
        result = SQLResult(
            rows=[{"id": 1, "name": "test"}],
            columns=["id", "name"],
            rows_affected=0,
        )

        assert len(result.rows) == 1
        assert result.columns == ["id", "name"]


class TestVendorRegistry:
    """Tests for vendor registry."""

    def test_register_and_get_vendor(self):
        """Test registering and retrieving a vendor."""
        class MockVendor(StorageVendor):
            @property
            def name(self):
                return "mock"

            @property
            def available_types(self):
                return [StorageType.KV]

            async def connect(self):
                return True

            async def disconnect(self):
                pass

            async def health(self):
                return {"healthy": True}

        register_vendor("mock", MockVendor)
        vendor = get_vendor("mock")

        assert vendor.name == "mock"
        assert StorageType.KV in vendor.available_types

    def test_get_unknown_vendor(self):
        """Test getting unknown vendor raises KeyError."""
        with pytest.raises(KeyError) as exc_info:
            get_vendor("nonexistent")

        assert "nonexistent" in str(exc_info.value)

    def test_list_vendors(self):
        """Test listing registered vendors."""
        vendors = list_vendors()
        assert "cloudflare" in vendors


class TestCloudflareConfig:
    """Tests for CloudflareConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = CloudflareConfig()

        assert config.gateway_url == "https://gateway.mumega.com/"
        assert config.timeout_seconds == 30.0
        assert config.vector_dimension == 1536

    def test_custom_config(self):
        """Test custom configuration."""
        config = CloudflareConfig(
            gateway_url="https://custom.gateway.com/",
            timeout_seconds=60.0,
            kv_namespace="CUSTOM_KV",
        )

        assert config.gateway_url == "https://custom.gateway.com/"
        assert config.kv_namespace == "CUSTOM_KV"


class TestGatewayClient:
    """Tests for GatewayClient."""

    @pytest.fixture
    def client(self):
        """Create a gateway client."""
        config = CloudflareConfig(gateway_url="https://test.gateway.com/")
        return GatewayClient(config)

    @pytest.mark.asyncio
    async def test_connect(self, client):
        """Test client connection."""
        await client.connect()
        assert client._client is not None
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect(self, client):
        """Test client disconnection."""
        await client.connect()
        await client.disconnect()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_request_success(self, client):
        """Test successful request."""
        with patch.object(client, "_client") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"success": True, "result": {"data": "test"}}
            mock_response.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            client._client = mock_client

            result = await client.request("test_action", {"key": "value"})

            assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_request_timeout(self, client):
        """Test request timeout handling."""
        import httpx

        with patch.object(client, "_client") as mock_client:
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            client._client = mock_client

            with pytest.raises(StorageError) as exc_info:
                await client.request("test_action", {})

            assert exc_info.value.code == StorageErrorCode.TIMEOUT


class TestCloudflareKVStore:
    """Tests for CloudflareKVStore."""

    @pytest.fixture
    def kv_store(self):
        """Create a KV store with mock client."""
        mock_client = MagicMock()
        mock_client.request = AsyncMock()
        return CloudflareKVStore(mock_client, "test-namespace")

    @pytest.mark.asyncio
    async def test_get(self, kv_store):
        """Test get value."""
        kv_store.client.request.return_value = {"value": "test-value"}

        result = await kv_store.get("test-key")

        assert result == "test-value"
        kv_store.client.request.assert_called_with("kv_get", {
            "namespace": "test-namespace",
            "key": "test-key",
        })

    @pytest.mark.asyncio
    async def test_get_not_found(self, kv_store):
        """Test get returns None for not found."""
        kv_store.client.request.side_effect = StorageError(
            StorageErrorCode.NOT_FOUND, "Not found"
        )

        result = await kv_store.get("missing-key")

        assert result is None

    @pytest.mark.asyncio
    async def test_put(self, kv_store):
        """Test put value."""
        kv_store.client.request.return_value = {}

        result = await kv_store.put("test-key", {"data": "value"}, ttl_seconds=3600)

        assert result is True
        kv_store.client.request.assert_called_with("kv_put", {
            "namespace": "test-namespace",
            "key": "test-key",
            "value": {"data": "value"},
            "expirationTtl": 3600,
        })

    @pytest.mark.asyncio
    async def test_delete(self, kv_store):
        """Test delete key."""
        kv_store.client.request.return_value = {}

        result = await kv_store.delete("test-key")

        assert result is True

    @pytest.mark.asyncio
    async def test_list(self, kv_store):
        """Test list keys."""
        kv_store.client.request.return_value = {
            "keys": [{"name": "key1"}, {"name": "key2"}],
            "cursor": "next-page",
        }

        keys, cursor = await kv_store.list(prefix="test:", limit=10)

        assert keys == ["key1", "key2"]
        assert cursor == "next-page"

    @pytest.mark.asyncio
    async def test_exists(self, kv_store):
        """Test exists check."""
        kv_store.client.request.return_value = {"value": "exists"}

        result = await kv_store.exists("test-key")

        assert result is True


class TestCloudflareObjectStore:
    """Tests for CloudflareObjectStore."""

    @pytest.fixture
    def object_store(self):
        """Create an Object store with mock client."""
        mock_client = MagicMock()
        mock_client.request = AsyncMock()
        return CloudflareObjectStore(mock_client, "test-bucket")

    @pytest.mark.asyncio
    async def test_get(self, object_store):
        """Test get object."""
        import base64
        content = b"test content"
        object_store.client.request.return_value = {
            "body": base64.b64encode(content).decode()
        }

        result = await object_store.get("test.txt")

        assert result == content

    @pytest.mark.asyncio
    async def test_head(self, object_store):
        """Test get object metadata."""
        object_store.client.request.return_value = {
            "size": 1024,
            "httpMetadata": {"contentType": "text/plain"},
            "etag": "abc123",
        }

        result = await object_store.head("test.txt")

        assert result.size == 1024
        assert result.content_type == "text/plain"

    @pytest.mark.asyncio
    async def test_put(self, object_store):
        """Test put object."""
        object_store.client.request.return_value = {"etag": "new-etag"}

        result = await object_store.put(
            "test.txt",
            b"content",
            content_type="text/plain",
        )

        assert result.key == "test.txt"
        assert result.etag == "new-etag"

    @pytest.mark.asyncio
    async def test_delete(self, object_store):
        """Test delete object."""
        object_store.client.request.return_value = {}

        result = await object_store.delete("test.txt")

        assert result is True


class TestCloudflareSQLStore:
    """Tests for CloudflareSQLStore."""

    @pytest.fixture
    def sql_store(self):
        """Create a SQL store with mock client."""
        mock_client = MagicMock()
        mock_client.request = AsyncMock()
        return CloudflareSQLStore(mock_client, "test-db")

    @pytest.mark.asyncio
    async def test_execute(self, sql_store):
        """Test execute SQL."""
        sql_store.client.request.return_value = {
            "results": [{"id": 1}],
            "columns": ["id"],
            "changes": 1,
        }

        result = await sql_store.execute(
            "INSERT INTO test (name) VALUES (?)",
            ["test-name"],
        )

        assert result.rows_affected == 1

    @pytest.mark.asyncio
    async def test_query_one(self, sql_store):
        """Test query single row."""
        sql_store.client.request.return_value = {
            "results": [{"id": 1, "name": "test"}],
            "columns": ["id", "name"],
        }

        result = await sql_store.query_one("SELECT * FROM test WHERE id = ?", [1])

        assert result == {"id": 1, "name": "test"}

    @pytest.mark.asyncio
    async def test_query_one_empty(self, sql_store):
        """Test query single row returns None when empty."""
        sql_store.client.request.return_value = {
            "results": [],
            "columns": ["id"],
        }

        result = await sql_store.query_one("SELECT * FROM test WHERE id = ?", [999])

        assert result is None

    @pytest.mark.asyncio
    async def test_table_exists(self, sql_store):
        """Test table exists check."""
        sql_store.client.request.return_value = {
            "results": [{"name": "test_table"}],
            "columns": ["name"],
        }

        result = await sql_store.table_exists("test_table")

        assert result is True


class TestCloudflareVectorStore:
    """Tests for CloudflareVectorStore."""

    @pytest.fixture
    def vector_store(self):
        """Create a Vector store with mock client."""
        mock_client = MagicMock()
        mock_client.request = AsyncMock()
        return CloudflareVectorStore(mock_client, "test-index", dimension=384)

    def test_dimension(self, vector_store):
        """Test dimension property."""
        assert vector_store.dimension == 384

    @pytest.mark.asyncio
    async def test_upsert(self, vector_store):
        """Test upsert vectors."""
        vector_store.client.request.return_value = {"count": 2}

        vectors = [
            VectorEntry(id="v1", vector=[0.1, 0.2], metadata={}),
            VectorEntry(id="v2", vector=[0.3, 0.4], metadata={}),
        ]

        result = await vector_store.upsert(vectors)

        assert result == 2

    @pytest.mark.asyncio
    async def test_query(self, vector_store):
        """Test query vectors."""
        vector_store.client.request.return_value = {
            "matches": [
                {"id": "v1", "score": 0.95, "metadata": {"label": "a"}},
                {"id": "v2", "score": 0.85, "metadata": {"label": "b"}},
            ]
        }

        result = await vector_store.query([0.1, 0.2], top_k=5)

        assert len(result) == 2
        assert result[0].score == 0.95
        assert result[0].id == "v1"

    @pytest.mark.asyncio
    async def test_delete(self, vector_store):
        """Test delete vectors."""
        vector_store.client.request.return_value = {"count": 2}

        result = await vector_store.delete(["v1", "v2"])

        assert result == 2

    @pytest.mark.asyncio
    async def test_count(self, vector_store):
        """Test count vectors."""
        vector_store.client.request.return_value = {"vectorCount": 100}

        result = await vector_store.count()

        assert result == 100


class TestCloudflareStorageVendor:
    """Tests for CloudflareStorageVendor."""

    @pytest.fixture
    def vendor(self):
        """Create a Cloudflare vendor."""
        return CloudflareStorageVendor(gateway_url="https://test.gateway.com/")

    def test_name(self, vendor):
        """Test vendor name."""
        assert vendor.name == "cloudflare"

    def test_available_types(self, vendor):
        """Test available storage types."""
        types = vendor.available_types

        assert StorageType.KV in types
        assert StorageType.OBJECT in types
        assert StorageType.SQL in types
        assert StorageType.VECTOR in types

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, vendor):
        """Test connect and disconnect."""
        with patch.object(vendor._client, "connect", new_callable=AsyncMock):
            result = await vendor.connect()
            assert result is True
            assert vendor._connected is True

        with patch.object(vendor._client, "disconnect", new_callable=AsyncMock):
            await vendor.disconnect()
            assert vendor._connected is False

    def test_get_kv_store(self, vendor):
        """Test getting KV store."""
        kv = vendor.kv("test-ns")

        assert isinstance(kv, CloudflareKVStore)
        # Same namespace returns same instance
        assert vendor.kv("test-ns") is kv

    def test_get_object_store(self, vendor):
        """Test getting Object store."""
        objects = vendor.objects("test-bucket")

        assert isinstance(objects, CloudflareObjectStore)

    def test_get_sql_store(self, vendor):
        """Test getting SQL store."""
        sql = vendor.sql("test-db")

        assert isinstance(sql, CloudflareSQLStore)

    def test_get_vector_store(self, vendor):
        """Test getting Vector store."""
        vectors = vendor.vectors("test-index")

        assert isinstance(vectors, CloudflareVectorStore)


class TestCreateCloudflareVendor:
    """Tests for create_cloudflare_vendor factory."""

    def test_with_url(self):
        """Test factory with URL."""
        vendor = create_cloudflare_vendor(gateway_url="https://custom.com/")

        assert vendor.config.gateway_url == "https://custom.com/"

    def test_with_env_default(self, monkeypatch):
        """Test factory uses env variable."""
        monkeypatch.setenv("GATEWAY_URL", "https://env.gateway.com/")

        vendor = create_cloudflare_vendor()

        assert vendor.config.gateway_url == "https://env.gateway.com/"

    def test_with_extra_config(self):
        """Test factory with extra config."""
        vendor = create_cloudflare_vendor(
            gateway_url="https://test.com/",
            kv_namespace="CUSTOM_NS",
            timeout_seconds=60.0,
        )

        assert vendor.config.kv_namespace == "CUSTOM_NS"
        assert vendor.config.timeout_seconds == 60.0
