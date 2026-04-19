"""GraphPort — knowledge graph (nodes, edges, cross-tenant links).

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for the content/knowledge graph that powers topic pages,
wikilinks, backlinks, and the cross-tenant network view.

Tenant binding: EXPLICIT tenant_id, OPTIONAL on every read. Nodes and
edges are tenant-owned; public nodes may be queried across tenants
(see resolveCrossTenantEdges and the queryNetwork convention on Inkwell).
We pass tenant_id explicitly to match Inkwell v6.2.

Divergence from Inkwell
-----------------------
Inkwell's GraphPort has `ingest(data: GraphData)` and `queryNetwork(filter)`.
The SOS spec for this task asks for upsert_node / upsert_edge /
get_node / get_backlinks / get_neighbors / query_nodes /
resolve_cross_tenant_edges. We implement that exact set and OMIT ingest()
and queryNetwork() — they can be added in a follow-up without breaking
existing adapters. Mark this gap in the Phase 1 retrospective.
"""
from __future__ import annotations

from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


Visibility = Literal["public", "private"]
EdgeType = Literal["wikilink", "tag", "series", "backlink", "cross-tenant"]


# --- Request / response models ---------------------------------------------


class GraphNode(BaseModel):
    """Directly mirrors Inkwell's GraphNode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(min_length=1)
    title: str
    type: str = Field(description="'blog' | 'topic' | 'concept' | 'lab' | ...")
    tags: list[str] = Field(default_factory=list)
    tenant: Optional[str] = Field(
        default=None,
        description="Which tenant owns this node. None = platform-level.",
    )
    visibility: Visibility = "private"
    author: Optional[str] = None
    date: Optional[str] = None
    url: Optional[str] = None


class GraphEdge(BaseModel):
    """Directly mirrors Inkwell's GraphEdge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(description="Source node slug.")
    target: str = Field(description="Target node slug.")
    type: EdgeType
    tenant: Optional[str] = None
    weight: Optional[float] = None


class UpsertNodeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Optional[str] = None
    node: GraphNode


class UpsertEdgeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Optional[str] = None
    edge: GraphEdge


class GetNodeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Optional[str] = None
    id: str = Field(min_length=1, description="Node slug.")


class GetBacklinksRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Optional[str] = None
    id: str = Field(min_length=1, description="Target node slug.")


class GetNeighborsRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Optional[str] = None
    id: str = Field(min_length=1, description="Center node slug.")
    depth: int = Field(default=1, ge=1, le=5)


class GraphData(BaseModel):
    """Bundle of nodes + edges returned from neighbor queries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class NodeFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tag: Optional[str] = None
    type: Optional[str] = None
    tenant: Optional[str] = None
    visibility: Optional[Visibility] = None
    limit: Optional[int] = Field(default=None, ge=1, le=10000)


class QueryNodesRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Optional[str] = None
    filters: NodeFilters


class ResolveCrossTenantRequest(BaseModel):
    """After ingest, resolve wikilinks to public nodes in OTHER tenants and
    materialize cross-tenant edges. Mirrors Inkwell's resolveCrossTenantEdges."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(description="Owning tenant of the source node.")
    source_slug: str = Field(
        description="Node whose wikilinks we're resolving."
    )
    wikilinks: list[str] = Field(
        description="Raw wikilink targets extracted from the source content."
    )


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class GraphPort(Protocol):
    """Tenant-aware knowledge graph."""

    async def upsert_node(self, req: UpsertNodeRequest) -> None:
        """Insert or update a node by (slug, tenant)."""
        ...

    async def upsert_edge(self, req: UpsertEdgeRequest) -> None:
        """Insert or update an edge."""
        ...

    async def get_node(self, req: GetNodeRequest) -> Optional[GraphNode]:
        """Fetch a node by slug (and optional tenant) — None if missing."""
        ...

    async def get_backlinks(self, req: GetBacklinksRequest) -> list[GraphEdge]:
        """All edges whose target is `req.id`."""
        ...

    async def get_neighbors(self, req: GetNeighborsRequest) -> GraphData:
        """Nodes + edges within `depth` hops of the center node."""
        ...

    async def query_nodes(self, req: QueryNodesRequest) -> list[GraphNode]:
        """Filtered node query."""
        ...

    async def resolve_cross_tenant_edges(
        self, req: ResolveCrossTenantRequest
    ) -> list[GraphEdge]:
        """Materialize cross-tenant edges from a newly ingested node's wikilinks."""
        ...


__all__ = [
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
    "GraphPort",
]
