"""
Storage client for file operations.

This module provides a controlled interface for file storage operations
through the ToRivers platform. Direct filesystem access is not allowed
in sandboxed environments.

Files are stored in execution-scoped buckets:
- Isolated per execution
- Auto-cleaned after retention period
- Publicly accessible URLs for outputs
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Default storage limits
DEFAULT_MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_TOTAL_SIZE: int = 500 * 1024 * 1024  # 500 MB per execution
DEFAULT_RETENTION_DAYS: int = 7

# Common MIME types
MIME_TYPES: dict[str, str] = {
    # Documents
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # Text
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".md": "text/markdown",
    # Images
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    # Archives
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    # Other
    ".bin": "application/octet-stream",
}


def guess_content_type(filename: str) -> str:
    """
    Guess MIME type from filename extension.

    Args:
        filename: Filename to check

    Returns:
        MIME type string, defaults to "application/octet-stream"
    """
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return MIME_TYPES.get(ext, "application/octet-stream")


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class StorageFile:
    """Metadata for a stored file."""

    path: str
    size_bytes: int
    content_type: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    url: str | None = None

    @property
    def filename(self) -> str:
        """Extract filename from path."""
        return self.path.rsplit("/", 1)[-1]

    @property
    def extension(self) -> str:
        """Extract file extension."""
        if "." in self.filename:
            return "." + self.filename.rsplit(".", 1)[-1].lower()
        return ""

    @property
    def size_kb(self) -> float:
        """Size in kilobytes."""
        return self.size_bytes / 1024

    @property
    def size_mb(self) -> float:
        """Size in megabytes."""
        return self.size_bytes / (1024 * 1024)


@dataclass
class StorageQuota:
    """Storage quota information for an execution."""

    used_bytes: int = 0
    max_bytes: int = DEFAULT_MAX_TOTAL_SIZE
    file_count: int = 0
    max_file_size: int = DEFAULT_MAX_FILE_SIZE

    @property
    def available_bytes(self) -> int:
        """Available storage in bytes."""
        return max(0, self.max_bytes - self.used_bytes)

    @property
    def usage_percent(self) -> float:
        """Storage usage as percentage (0-100)."""
        if self.max_bytes == 0:
            return 100.0
        return (self.used_bytes / self.max_bytes) * 100

    def can_store(self, size_bytes: int) -> bool:
        """Check if a file of given size can be stored."""
        if size_bytes > self.max_file_size:
            return False
        return size_bytes <= self.available_bytes


# =============================================================================
# Exceptions
# =============================================================================


class StorageError(Exception):
    """Base error from storage service."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


class StorageFileNotFoundError(StorageError):
    """Raised when a requested file does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"File not found: {path}",
            code="not_found",
            path=path,
        )


class StorageQuotaExceededError(StorageError):
    """Raised when storage quota is exceeded."""

    def __init__(
        self,
        message: str = "Storage quota exceeded",
        used_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        super().__init__(message, code="quota_exceeded")
        self.used_bytes = used_bytes
        self.max_bytes = max_bytes


class StorageFileTooLargeError(StorageError):
    """Raised when a file exceeds the maximum allowed size."""

    def __init__(
        self,
        path: str,
        size_bytes: int,
        max_size_bytes: int = DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        size_mb = size_bytes / (1024 * 1024)
        max_mb = max_size_bytes / (1024 * 1024)
        super().__init__(
            f"File '{path}' is too large ({size_mb:.2f} MB). "
            f"Maximum allowed size is {max_mb:.2f} MB.",
            code="file_too_large",
            path=path,
        )
        self.size_bytes = size_bytes
        self.max_size_bytes = max_size_bytes


class StorageInvalidContentTypeError(StorageError):
    """Raised when a file has an invalid or disallowed content type."""

    def __init__(self, path: str, content_type: str) -> None:
        super().__init__(
            f"Invalid content type '{content_type}' for file '{path}'",
            code="invalid_content_type",
            path=path,
        )
        self.content_type = content_type


class StorageAccessDeniedError(StorageError):
    """Raised when access to storage is denied."""

    def __init__(self, path: str | None = None) -> None:
        message = "Storage access denied"
        if path:
            message = f"Storage access denied for: {path}"
        super().__init__(message, code="access_denied", path=path)


# =============================================================================
# Storage Client Interface
# =============================================================================


class StorageClient(ABC):
    """
    Client interface for file storage.

    Automations use this client to store and retrieve files through
    the ToRivers storage proxy. This ensures:
    - Size limits enforcement
    - Content type validation
    - Virus scanning
    - Storage quota management

    Files are stored in execution-scoped buckets:
    - Isolated per execution
    - Auto-cleaned after retention period
    - Publicly accessible URLs for outputs

    The actual implementation is injected by the runtime.

    Example:
        async def save_report(self, state: MyState) -> dict:
            storage = StorageClient.get_default()

            # Upload a file and get public URL
            url = await storage.upload(
                filename="report.pdf",
                content=pdf_bytes,
                content_type="application/pdf"
            )

            return {
                "output_data": {
                    "_version": 1,
                    "_blocks": [
                        {
                            "type": "json_data",
                            "label": "Stored File",
                            "data": {"report_url": url},
                            "display_hint": "key_value",
                        }
                    ],
                }
            }
    """

    @abstractmethod
    async def put(
        self,
        path: str,
        data: bytes,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Store a file.

        Args:
            path: Relative path for the file
            data: File content as bytes
            content_type: MIME type (auto-detected if not provided)
            metadata: Optional metadata to store with file

        Returns:
            Full storage path

        Raises:
            StorageError: If storage fails
            StorageFileTooLargeError: If file exceeds size limit
            StorageQuotaExceededError: If quota exceeded
        """
        pass

    @abstractmethod
    async def get(self, path: str) -> bytes:
        """
        Retrieve a file.

        Args:
            path: Storage path

        Returns:
            File content as bytes

        Raises:
            StorageFileNotFoundError: If file not found
            StorageError: If retrieval fails
        """
        pass

    @abstractmethod
    async def get_info(self, path: str) -> StorageFile:
        """
        Get file metadata without downloading.

        Args:
            path: Storage path

        Returns:
            File metadata

        Raises:
            StorageFileNotFoundError: If file not found
        """
        pass

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """
        Delete a file.

        Args:
            path: Storage path

        Returns:
            True if deleted, False if not found

        Raises:
            StorageError: If deletion fails
        """
        pass

    @abstractmethod
    async def list(
        self,
        prefix: str = "",
        limit: int = 100,
    ) -> list[StorageFile]:
        """
        List files with optional prefix filter.

        Args:
            prefix: Path prefix to filter by
            limit: Maximum number of files to return

        Returns:
            List of file metadata

        Raises:
            StorageError: If listing fails
        """
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """
        Check if a file exists.

        Args:
            path: Storage path

        Returns:
            True if file exists
        """
        pass

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------

    async def upload(
        self,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        """
        Upload a file and return a public URL.

        This is a convenience method that stores a file and returns
        its public URL for inclusion in automation outputs.

        Args:
            filename: Name for the file (not a path)
            content: File content as bytes
            content_type: MIME type (auto-detected if not provided)

        Returns:
            Public URL to access the file

        Raises:
            StorageError: If upload fails
            StorageFileTooLargeError: If file exceeds size limit
        """
        if content_type is None:
            content_type = guess_content_type(filename)

        path = await self.put(filename, content, content_type)
        return await self.get_url(path)

    async def download(self, filename: str) -> bytes:
        """
        Download a file by filename.

        This is a convenience method for retrieving files.

        Args:
            filename: Name of file to download

        Returns:
            File content as bytes

        Raises:
            StorageFileNotFoundError: If file not found
        """
        return await self.get(filename)

    async def list_files(self, prefix: str = "") -> list[str]:
        """
        List filenames in storage.

        This is a convenience method that returns just filenames
        instead of full StorageFile metadata.

        Args:
            prefix: Optional path prefix to filter by

        Returns:
            List of filenames
        """
        files = await self.list(prefix)
        return [f.filename for f in files]

    async def get_url(self, path: str) -> str:
        """
        Get public URL for a stored file.

        Args:
            path: Storage path

        Returns:
            Public URL to access the file

        Raises:
            StorageFileNotFoundError: If file not found
        """
        info = await self.get_info(path)
        if info.url:
            return info.url
        # Default behavior - subclasses should override for proper URLs
        return f"storage://{path}"

    async def get_quota(self) -> StorageQuota:
        """
        Get current storage quota information.

        Returns:
            StorageQuota with usage information
        """
        files = await self.list(limit=10000)
        used_bytes = sum(f.size_bytes for f in files)
        return StorageQuota(
            used_bytes=used_bytes,
            file_count=len(files),
        )

    @staticmethod
    def get_default() -> "StorageClient":
        """
        Get the default storage client.

        When ``TORIVERS_STORAGE_PROXY_URL`` and ``TORIVERS_STORAGE_TOKEN``
        environment variables are set, returns an :class:`HttpStorageClient`
        backed by the storage proxy.  For testing, use ``MockStorageClient``.

        Returns:
            Default storage client instance

        Raises:
            RuntimeError: If called outside runtime environment
        """
        base_url = os.environ.get("TORIVERS_STORAGE_PROXY_URL")
        token = os.environ.get("TORIVERS_STORAGE_TOKEN")
        if base_url and token:
            return HttpStorageClient(base_url=base_url, token=token)

        raise RuntimeError(
            "StorageClient.get_default() can only be called in the runtime environment. "
            "Set TORIVERS_STORAGE_PROXY_URL and TORIVERS_STORAGE_TOKEN, or "
            "use torivers_sdk.testing.mocks.MockStorageClient for testing."
        )


# =============================================================================
# HTTP Storage Client (concrete implementation)
# =============================================================================


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 datetime string, handling the ``Z`` suffix."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HttpStorageClient(StorageClient):
    """
    Storage client backed by the storage proxy REST API.

    Sends authenticated requests to the storage proxy's ``/api/storage/*``
    endpoints using short-lived JWT tokens for execution-scoped file
    operations.

    Args:
        base_url: Storage proxy base URL (e.g. ``http://localhost:8000``).
        token: JWT token for authentication.
        timeout: HTTP request timeout in seconds.

    Example::

        async with HttpStorageClient(
            base_url="http://proxy:8000",
            token=jwt_token,
        ) as storage:
            url = await storage.upload("report.pdf", pdf_bytes)
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Return (lazily created) ``httpx.AsyncClient``."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "HttpStorageClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # -- helpers --------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def _request(
        self,
        method: str,
        url_path: str,
        *,
        storage_path: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Execute an HTTP request, translating transport errors."""
        client = self._get_client()
        try:
            return await client.request(
                method,
                self._url(url_path),
                headers=self._headers(),
                **kwargs,
            )
        except httpx.TimeoutException:
            raise StorageError(
                "Storage request timed out",
                code="timeout",
                path=storage_path,
            )
        except httpx.HTTPError as exc:
            raise StorageError(
                f"Storage connection error: {exc}",
                code="connection_error",
                path=storage_path,
            )

    def _raise_for_status(
        self,
        response: httpx.Response,
        path: str | None = None,
    ) -> None:
        """Map HTTP error responses to SDK storage exceptions."""
        if response.is_success:
            return

        detail = ""
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text

        status = response.status_code
        if status == 401:
            raise StorageAccessDeniedError(path=path)
        if status == 404:
            raise StorageFileNotFoundError(path or "unknown")
        if status == 413:
            raise StorageFileTooLargeError(
                path=path or "unknown",
                size_bytes=0,
                max_size_bytes=DEFAULT_MAX_FILE_SIZE,
            )
        if status == 507:
            raise StorageQuotaExceededError(
                message=detail or "Storage quota exceeded",
            )

        raise StorageError(
            detail or f"Storage operation failed (HTTP {status})",
            code=str(status),
            path=path,
        )

    # -- abstract method implementations --------------------------------------

    async def put(
        self,
        path: str,
        data: bytes,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if content_type is None:
            content_type = guess_content_type(path)

        response = await self._request(
            "POST",
            "/api/storage/upload",
            storage_path=path,
            files={"file": (path, data, content_type)},
            params={"filename": path},
        )
        self._raise_for_status(response, path)
        return response.json()["path"]

    async def get(self, path: str) -> bytes:
        response = await self._request(
            "GET",
            f"/api/storage/download/{path}",
            storage_path=path,
        )
        self._raise_for_status(response, path)
        return response.content

    async def get_info(self, path: str) -> StorageFile:
        response = await self._request(
            "GET",
            f"/api/storage/info/{path}",
            storage_path=path,
        )
        self._raise_for_status(response, path)

        data = response.json()
        return StorageFile(
            path=data["path"],
            size_bytes=data["size_bytes"],
            content_type=data["content_type"],
            created_at=_parse_datetime(data["created_at"]),
            updated_at=_parse_datetime(data["updated_at"]),
            url=data.get("url"),
        )

    async def delete(self, path: str) -> bool:
        response = await self._request(
            "DELETE",
            f"/api/storage/{path}",
            storage_path=path,
        )
        if response.status_code == 404:
            return False
        self._raise_for_status(response, path)
        return response.json().get("success", False)

    async def list(
        self,
        prefix: str = "",
        limit: int = 100,
    ) -> list[StorageFile]:
        response = await self._request(
            "GET",
            "/api/storage/list",
            params={"prefix": prefix, "limit": limit},
        )
        self._raise_for_status(response)

        result: list[StorageFile] = []
        for item in response.json()["files"]:
            result.append(
                StorageFile(
                    path=item["path"],
                    size_bytes=item.get("size_bytes", 0),
                    content_type=item.get("content_type", "application/octet-stream"),
                    created_at=_parse_datetime(item["created_at"]),
                    updated_at=_parse_datetime(item["updated_at"]),
                    url=item.get("url"),
                    metadata=item.get("metadata", {}),
                )
            )
        return result

    async def exists(self, path: str) -> bool:
        try:
            await self.get_info(path)
            return True
        except StorageFileNotFoundError:
            return False

    # -- overrides for efficiency ---------------------------------------------

    async def get_url(self, path: str) -> str:
        info = await self.get_info(path)
        if info.url:
            return info.url
        return f"storage://{path}"

    async def get_quota(self) -> StorageQuota:
        response = await self._request("GET", "/api/storage/quota")
        self._raise_for_status(response)

        data = response.json()
        return StorageQuota(
            used_bytes=data["used_bytes"],
            max_bytes=data["max_bytes"],
            file_count=data["file_count"],
            max_file_size=data.get("max_file_size", DEFAULT_MAX_FILE_SIZE),
        )
