"""StoragePort — blob / object storage (R2, S3, GCS, local filesystem).

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how plugins read and write large binary / file content.

Tenant binding: IMPLICIT. The underlying bucket / prefix is bound when the
adapter is constructed per-tenant. Kernel-internal callers that need
cross-tenant reach should use the richer sos.contracts.storage.ObjectStore.
"""
from __future__ import annotations

from typing import Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


# --- Request / response models ---------------------------------------------


class StorageGetRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)


class StorageGetResult(BaseModel):
    """Object contents + content-type. `body` is raw bytes; adapters that
    stream should spool chunks into bytes at this boundary (ports are strict,
    streaming is an adapter-internal optimization)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    body: bytes
    content_type: str = "application/octet-stream"


class StoragePutRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    key: str = Field(min_length=1)
    # `value` is bytes|str|bytearray — wire-level shape. Adapters decide how
    # to chunk on the way out.
    value: Union[bytes, bytearray, str]
    content_type: Optional[str] = None


class StorageDeleteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)


class StorageListRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: Optional[str] = None


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class StoragePort(Protocol):
    """Blob store. Tenant bound by adapter wiring."""

    async def get(self, req: StorageGetRequest) -> Optional[StorageGetResult]:
        """Fetch blob by key. Returns None if not found."""
        ...

    async def put(self, req: StoragePutRequest) -> None:
        """Store or overwrite a blob. No response body."""
        ...

    async def delete(self, req: StorageDeleteRequest) -> None:
        """Remove a blob by key. Idempotent — missing keys don't raise."""
        ...

    async def list(self, req: StorageListRequest) -> list[str]:
        """List keys matching the optional prefix."""
        ...


__all__ = [
    "StorageGetRequest",
    "StorageGetResult",
    "StoragePutRequest",
    "StorageDeleteRequest",
    "StorageListRequest",
    "StoragePort",
]
