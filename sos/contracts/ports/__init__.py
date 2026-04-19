"""SOS canonical port registry — Inkwell v7.0 parity layer.

ARCHITECTURE
------------
This package is the cross-language source of truth for the 13 hexagonal
ports that SOS (Python) and Inkwell (TypeScript) both implement. Every
port is expressed as:

  1. A `typing.Protocol` marked `@runtime_checkable`. Protocol (not ABC)
     because:
       - Ports cross a language boundary. Structural typing maps cleanly
         to TS interfaces — no inheritance required.
       - Adapters can satisfy a port by shape alone, without importing
         from sos.contracts.
       - `@runtime_checkable` still gives us `isinstance(x, BusPort)`
         when we need to verify a plug-in at load time.

  2. One Pydantic v2 `BaseModel` per argument bundle and one per return
     shape. Models are `frozen=True, extra="forbid"` so:
       - They serialize cleanly to JSON Schema (`model_json_schema()`)
         for the Phase 1 contract-export step.
       - They're hashable and thread-safe by construction.
       - Unknown fields fail loudly at validation time — we want the
         loud failure between Python and TS.

TENANT BINDING
--------------
Inkwell mixes two patterns; we follow each port's native convention:

  - Tenant-AWARE (tenant_id required on every method):
      economy.py, agent.py, crm.py, search.py, content.py
    These are billing / platform-plane ports. The Mothership calls them
    with explicit tenant context — no ambient session to lean on.

  - Tenant-AMBIENT (tenant bound at adapter construction, no tenant_id
    on method args):
      bus.py, memory.py, storage.py, database.py, session.py, auth.py,
      content_source.py
    These are per-request / per-tenant ports. The adapter is already
    scoped; passing tenant_id again would be noise.

  - Tenant-OPTIONAL (tenant_id accepted on reads, required on writes):
      graph.py
    The graph is partly public, partly private — Inkwell's v6.2
    resolveCrossTenantEdges proves we need both.

DIVERGENCE FROM INKWELL
-----------------------
The port surfaces here are the SOS-side canonical shapes. Where the
user-provided task spec asked for a surface broader than Inkwell
currently offers (e.g. CRMPort.send_message, ContentPort.invalidate,
tenant_id on SearchPort), we implement the broader surface and noted
the delta in the relevant file's module docstring. When Inkwell widens,
those notes become merge hints, not mysteries.

Re-exports below are the public API. Importers should say:

    from sos.contracts.ports import BusPort, ChargeRequest
"""
from __future__ import annotations

from sos.contracts.ports.agent import (
    AgentConfig,
    AgentConfigPatch,
    AgentModel,
    AgentPort,
    AgentStatus,
    AgentUsage,
    BudgetCheckRequest,
    BudgetCheckResult,
    GetConfigRequest,
    GetUsageRequest,
    McpServerRef,
    ProvisionRequest,
    RecordAgentUsageRequest,
    UpdateConfigRequest,
    UsageWindow,
)
from sos.contracts.ports.auth import (
    AuthPort,
    AuthUser,
    GetUserRequest,
    Role,
)
from sos.contracts.ports.bus import (
    BroadcastRequest,
    BusMessage,
    BusPort,
    BusSubscriber,
    InboxRequest,
    SendRequest,
    UnsubscribeHandle,
)
from sos.contracts.ports.content import (
    ContentGetRequest,
    ContentGetResult,
    ContentInvalidateRequest,
    ContentPort,
    ContentPutRequest,
)
from sos.contracts.ports.content_source import (
    ContentSourceItem,
    ContentSourcePort,
)
from sos.contracts.ports.content_source import ListRequest as ContentSourceListRequest
from sos.contracts.ports.content_source import SyncRequest as ContentSourceSyncRequest
from sos.contracts.ports.crm import (
    Contact,
    ContactData,
    CRMPort,
    CreateContactRequest,
    CreateContactResult,
    GetContactRequest,
    ListContactsFilters,
    ListContactsRequest,
    MessageContent,
    SendMessageRequest,
    SendMessageResult,
    UpdateContactRequest,
)
from sos.contracts.ports.database import (
    BatchRequest,
    BatchStatement,
    DatabasePort,
    ExecuteRequest,
    ExecuteResult,
    QueryRequest,
    Row,
)
from sos.contracts.ports.economy import (
    Balance,
    ChargeRequest,
    ChargeResult,
    EconomyPort,
    GetBalanceRequest,
    RecordUsageRequest,
    TransferRequest,
)
from sos.contracts.ports.graph import (
    EdgeType,
    GetBacklinksRequest,
    GetNeighborsRequest,
    GetNodeRequest,
    GraphData,
    GraphEdge,
    GraphNode,
    GraphPort,
    NodeFilters,
    QueryNodesRequest,
    ResolveCrossTenantRequest,
    UpsertEdgeRequest,
    UpsertNodeRequest,
    Visibility,
)
from sos.contracts.ports.media import (
    MediaAsset,
    MediaChapter,
    MediaDeleteRequest,
    MediaDescribeResult,
    MediaGenerateImageRequest,
    MediaGetRequest,
    MediaListRequest,
    MediaListResult,
    MediaPort,
    MediaSearchRequest,
    MediaTranscribeResult,
    MediaTransformRequest,
    MediaUploadRequest,
)
from sos.contracts.ports.memory import (
    MemoryPort,
    MemoryResult,
    RecallRequest,
    RememberRequest,
    RememberResult,
)
from sos.contracts.ports.memory import SearchRequest as MemorySearchRequest
from sos.contracts.ports.search import (
    DeleteSearchRequest,
    IndexRequest,
    SearchDoc,
    SearchHit,
    SearchPort,
)
from sos.contracts.ports.search import SearchRequest as SearchQueryRequest
from sos.contracts.ports.session import (
    SessionDeleteRequest,
    SessionGetRequest,
    SessionPort,
    SessionPutRequest,
)
from sos.contracts.ports.storage import (
    StorageDeleteRequest,
    StorageGetRequest,
    StorageGetResult,
    StorageListRequest,
    StoragePort,
    StoragePutRequest,
)


# The 14 ports — the canonical list Phase 1 exports.
ALL_PORTS: tuple[type, ...] = (
    BusPort,
    EconomyPort,
    MemoryPort,
    StoragePort,
    DatabasePort,
    SessionPort,
    AuthPort,
    AgentPort,
    ContentSourcePort,
    CRMPort,
    SearchPort,
    GraphPort,
    ContentPort,
    MediaPort,
)


__all__ = [
    # --- The 14 ports ---
    "BusPort",
    "EconomyPort",
    "MemoryPort",
    "StoragePort",
    "DatabasePort",
    "SessionPort",
    "AuthPort",
    "AgentPort",
    "ContentSourcePort",
    "CRMPort",
    "SearchPort",
    "GraphPort",
    "ContentPort",
    "MediaPort",
    "ALL_PORTS",
    # --- bus.py ---
    "BusMessage",
    "SendRequest",
    "BroadcastRequest",
    "InboxRequest",
    "UnsubscribeHandle",
    "BusSubscriber",
    # --- economy.py ---
    "RecordUsageRequest",
    "GetBalanceRequest",
    "Balance",
    "ChargeRequest",
    "TransferRequest",
    "ChargeResult",
    # --- memory.py ---
    "RememberRequest",
    "RememberResult",
    "RecallRequest",
    "MemorySearchRequest",
    "MemoryResult",
    # --- storage.py ---
    "StorageGetRequest",
    "StorageGetResult",
    "StoragePutRequest",
    "StorageDeleteRequest",
    "StorageListRequest",
    # --- database.py ---
    "QueryRequest",
    "ExecuteRequest",
    "ExecuteResult",
    "BatchStatement",
    "BatchRequest",
    "Row",
    # --- session.py ---
    "SessionGetRequest",
    "SessionPutRequest",
    "SessionDeleteRequest",
    # --- auth.py ---
    "Role",
    "AuthUser",
    "GetUserRequest",
    # --- agent.py ---
    "AgentModel",
    "AgentStatus",
    "UsageWindow",
    "McpServerRef",
    "AgentConfig",
    "AgentConfigPatch",
    "ProvisionRequest",
    "GetConfigRequest",
    "UpdateConfigRequest",
    "RecordAgentUsageRequest",
    "AgentUsage",
    "GetUsageRequest",
    "BudgetCheckRequest",
    "BudgetCheckResult",
    # --- content_source.py ---
    "ContentSourceItem",
    "ContentSourceListRequest",
    "ContentSourceSyncRequest",
    # --- crm.py ---
    "ContactData",
    "Contact",
    "CreateContactRequest",
    "CreateContactResult",
    "GetContactRequest",
    "UpdateContactRequest",
    "ListContactsFilters",
    "ListContactsRequest",
    "MessageContent",
    "SendMessageRequest",
    "SendMessageResult",
    # --- search.py ---
    "SearchDoc",
    "IndexRequest",
    "SearchQueryRequest",
    "SearchHit",
    "DeleteSearchRequest",
    # --- graph.py ---
    "Visibility",
    "EdgeType",
    "GraphNode",
    "GraphEdge",
    "GraphData",
    "NodeFilters",
    "UpsertNodeRequest",
    "UpsertEdgeRequest",
    "GetNodeRequest",
    "GetBacklinksRequest",
    "GetNeighborsRequest",
    "QueryNodesRequest",
    "ResolveCrossTenantRequest",
    # --- content.py ---
    "ContentGetRequest",
    "ContentGetResult",
    "ContentPutRequest",
    "ContentInvalidateRequest",
    # --- media.py ---
    "MediaChapter",
    "MediaAsset",
    "MediaUploadRequest",
    "MediaGetRequest",
    "MediaDescribeResult",
    "MediaTranscribeResult",
    "MediaTransformRequest",
    "MediaSearchRequest",
    "MediaListRequest",
    "MediaListResult",
    "MediaDeleteRequest",
    "MediaGenerateImageRequest",
]
