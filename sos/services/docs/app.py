"""
SOS Docs Service (sos-docs)

Owns the canonical doc-node graph with 5-tier RBAC.  All tier enforcement
flows through tier_filter.apply_tier_filter() — routes never re-check tiers.

Nodes invisible to the caller always return 404, never 403, to avoid leaking
their existence (§12 spec §4).

Port: SOS_DOCS_PORT env var, default 8085.
DB:   DATABASE_URL env var (PostgreSQL, same host as Mirror).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date, datetime
from typing import Any, Literal

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sos import __version__
from sos.kernel.audit_chain import AuditChainEvent, emit_audit
from sos.kernel.telemetry import init_tracing, instrument_fastapi

from .tier_filter import CallerContext, apply_tier_filter, is_coordinator, is_coordinator_or_author

# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

init_tracing("sos-docs")

app = FastAPI(title="SOS Docs Service", version=__version__)
instrument_fastapi(app)

_START_TIME = time.time()

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://mumega:mumega@localhost:5432/mumega",
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


@app.on_event("startup")
async def _startup() -> None:
    await get_pool()
    try:
        from sos.services.bus.discovery import register_service

        port = int(os.getenv("SOS_DOCS_PORT", "8085"))
        await register_service("sos-docs", port)
    except Exception:
        pass  # bus unavailable at startup is non-fatal


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _pool:
        await _pool.close()


# ---------------------------------------------------------------------------
# Auth — extract caller context from Authorization header
# ---------------------------------------------------------------------------

_SYSTEM_TOKEN: str = os.getenv("SOS_DOCS_TOKEN", "")


def _caller_from_token(token: str | None) -> CallerContext:
    """Build a CallerContext from a raw bearer token.

    In production the token is a signed JWT or an opaque API key validated
    by the kernel's auth gateway.  For now the service trusts the claims
    embedded in the token (or falls back to unauthenticated / system).

    Tokens are expected to carry X-Caller-* headers rather than JWT claims
    in this initial scaffolding — the auth gateway injects those headers
    after validating the bearer token.
    """
    if not token:
        return CallerContext()
    if token == _SYSTEM_TOKEN:
        return CallerContext(is_system=True)
    # Non-system tokens: roles + entity/project claims come from injected
    # headers (see _resolve_caller below).
    return CallerContext()


def _resolve_caller(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_caller_roles: str | None = Header(default=None, alias="X-Caller-Roles"),
    x_caller_entity_id: str | None = Header(default=None, alias="X-Caller-Entity-Id"),
    x_caller_project_id: str | None = Header(default=None, alias="X-Caller-Project-Id"),
    x_caller_squad_ids: str | None = Header(default=None, alias="X-Caller-Squad-Ids"),
) -> CallerContext:
    """FastAPI dependency — builds CallerContext from request headers.

    The auth gateway validates the bearer token and injects X-Caller-*
    headers before the request reaches this service.  If there is no
    gateway (local dev), the system token bypasses all checks.
    """
    raw_token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()

    is_system = raw_token == _SYSTEM_TOKEN

    roles: frozenset[str] = frozenset(
        r.strip() for r in (x_caller_roles or "").split(",") if r.strip()
    )
    squad_ids: frozenset[str] = frozenset(
        s.strip() for s in (x_caller_squad_ids or "").split(",") if s.strip()
    )

    return CallerContext(
        roles=roles,
        entity_id=x_caller_entity_id or None,
        project_id=x_caller_project_id or None,
        squad_ids=squad_ids,
        is_system=is_system,
    )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

VALID_TIERS = Literal["public", "squad", "project", "role", "entity", "private"]
VALID_EDGE_TYPES = Literal[
    "articulates", "derives_from", "sequences", "specced_in", "supersedes", "exemplifies"
]


class NodeCreate(BaseModel):
    id: str = Field(..., description="Stable slug, e.g. 'sos/stack-sections/12-sos-docs'")
    tier: VALID_TIERS = "project"
    entity_id: str | None = None
    permitted_roles: list[str] = Field(default_factory=list)
    project_id: str | None = None
    squad_id: str | None = None
    author_id: str
    title: str
    summary: str | None = None
    body: str
    body_format: Literal["markdown", "mdx", "plaintext"] = "markdown"
    frontmatter: dict[str, Any] | None = None
    version: str = "1.0"
    supersedes: str | None = None


class TierPatch(BaseModel):
    tier: VALID_TIERS


class RelationCreate(BaseModel):
    to_node: str
    edge_type: VALID_EDGE_TYPES
    weight: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    result = {}
    for k, v in dict(row).items():
        if isinstance(v, (datetime, date)):
            result[k] = v.isoformat()
        elif isinstance(v, (bytes, memoryview)):
            result[k] = None  # binary columns (hash, signature) not exposed in list
        else:
            result[k] = v
    return result


async def _node_visible(
    node_id: str,
    caller: CallerContext,
    pool: asyncpg.Pool,
) -> dict[str, Any] | None:
    """Return the node dict if visible to caller, else None (never raises)."""
    where, params = apply_tier_filter(caller)
    query = f"""
        SELECT * FROM docs_nodes n
        WHERE n.id = $1
          AND {where.replace('%s', '$' + '{}').format(*range(2, 2 + len(params)))}
    """
    # asyncpg uses $N positional placeholders
    query = _build_asyncpg_query(
        "SELECT * FROM docs_nodes n WHERE n.id = $1 AND {tier_filter}",
        where,
        params,
        extra_positional=1,
    )
    row = await pool.fetchrow(query, node_id, *params)
    return dict(row) if row else None


def _build_asyncpg_query(
    template: str,
    where_fragment: str,
    params: list[Any],
    extra_positional: int = 0,
) -> str:
    """Replace ``%s`` placeholders in *where_fragment* with ``$N`` style.

    asyncpg uses ``$1``, ``$2``, … positional placeholders, not ``%s``.
    *extra_positional* is the number of ``$N`` placeholders already present in
    *template* before the tier filter params begin (e.g. ``$1`` for node_id).
    """
    offset = extra_positional + 1
    result = where_fragment
    for i in range(len(params)):
        result = result.replace("%s", f"${offset + i}", 1)
    return template.replace("{tier_filter}", result)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "sos-docs",
        "version": __version__,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
    }


@app.get("/docs/nodes")
async def list_nodes(
    tier: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    caller: CallerContext = Depends(_resolve_caller),
    pool: asyncpg.Pool = Depends(get_pool),
) -> JSONResponse:
    """List nodes visible to the caller.

    Applies tier filter first, then optional user-supplied filters.
    """
    where, params = apply_tier_filter(caller)

    conditions = [where]
    if tier:
        params.append(tier)
        conditions.append(f"n.tier = ${len(params) + 0}")
    if project_id:
        params.append(project_id)
        conditions.append(f"n.project_id = ${len(params) + 0}")
    if entity_id:
        params.append(entity_id)
        conditions.append(f"n.entity_id = ${len(params) + 0}")

    # Build the full WHERE clause with properly numbered $N placeholders
    tier_where, tier_params = apply_tier_filter(caller)
    extra_params: list[Any] = []
    extra_clauses: list[str] = []

    if tier:
        extra_params.append(tier)
        extra_clauses.append("n.tier = %s")
    if project_id:
        extra_params.append(project_id)
        extra_clauses.append("n.project_id = %s")
    if entity_id:
        extra_params.append(entity_id)
        extra_clauses.append("n.entity_id = %s")

    all_params = tier_params + extra_params
    all_params_plus = all_params + [limit, offset]

    # Build raw query with $N placeholders
    raw_tier = tier_where
    offset_n = 0
    for i in range(len(tier_params)):
        raw_tier = raw_tier.replace("%s", f"${i + 1}", 1)
    offset_n = len(tier_params)

    extra_sql_parts: list[str] = []
    for j, clause in enumerate(extra_clauses):
        numbered = clause.replace("%s", f"${offset_n + j + 1}", 1)
        extra_sql_parts.append(numbered)

    all_conditions = [raw_tier] + extra_sql_parts
    where_sql = " AND ".join(all_conditions)

    limit_ph = f"${len(all_params) + 1}"
    offset_ph = f"${len(all_params) + 2}"

    query = f"""
        SELECT * FROM docs_nodes n
        WHERE {where_sql}
        ORDER BY n.updated_at DESC
        LIMIT {limit_ph} OFFSET {offset_ph}
    """

    rows = await pool.fetch(query, *all_params_plus)
    return JSONResponse({"nodes": [_row_to_dict(r) for r in rows]})


@app.get("/docs/nodes/{node_id}")
async def get_node(
    node_id: str,
    caller: CallerContext = Depends(_resolve_caller),
    pool: asyncpg.Pool = Depends(get_pool),
) -> JSONResponse:
    """Return a single node — 404 if invisible (never 403)."""
    tier_where, tier_params = apply_tier_filter(caller)

    raw_tier = tier_where
    for i in range(len(tier_params)):
        raw_tier = raw_tier.replace("%s", f"${i + 2}", 1)

    query = f"""
        SELECT * FROM docs_nodes n
        WHERE n.id = $1
          AND {raw_tier}
    """
    row = await pool.fetchrow(query, node_id, *tier_params)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return JSONResponse(_row_to_dict(row))


@app.get("/docs/nodes/{node_id}/relations")
async def get_relations(
    node_id: str,
    caller: CallerContext = Depends(_resolve_caller),
    pool: asyncpg.Pool = Depends(get_pool),
) -> JSONResponse:
    """Return edges where *node_id* is source or target.

    Both the anchor node and each related node must be visible to the caller.
    Edges pointing to invisible nodes are silently excluded (never 403).
    """
    tier_where, tier_params = apply_tier_filter(caller)

    # First confirm the anchor node is visible
    anchor_tier_raw = tier_where
    for i in range(len(tier_params)):
        anchor_tier_raw = anchor_tier_raw.replace("%s", f"${i + 2}", 1)

    anchor_q = f"""
        SELECT id FROM docs_nodes n
        WHERE n.id = $1 AND {anchor_tier_raw}
    """
    anchor = await pool.fetchrow(anchor_q, node_id, *tier_params)
    if anchor is None:
        raise HTTPException(status_code=404, detail="not_found")

    # Fetch relations where the related-node is also visible.
    # We join against docs_nodes for both endpoints and apply the tier filter
    # on the *other* node (from_node or to_node depending on direction).
    # $1 = node_id; tier params start at $2.
    other_tier_raw = tier_where
    for i in range(len(tier_params)):
        other_tier_raw = other_tier_raw.replace("%s", f"${i + 2}", 1)

    relations_q = f"""
        SELECT r.id, r.from_node, r.to_node, r.edge_type, r.weight, r.created_at
        FROM docs_relations r
        JOIN docs_nodes n ON n.id = CASE
            WHEN r.from_node = $1 THEN r.to_node
            ELSE r.from_node
        END
        WHERE (r.from_node = $1 OR r.to_node = $1)
          AND {other_tier_raw}
        ORDER BY r.created_at DESC
    """
    rows = await pool.fetch(relations_q, node_id, *tier_params)
    return JSONResponse({"relations": [_row_to_dict(r) for r in rows]})


@app.post("/docs/nodes", status_code=201)
async def create_node(
    body: NodeCreate,
    caller: CallerContext = Depends(_resolve_caller),
    pool: asyncpg.Pool = Depends(get_pool),
) -> JSONResponse:
    """Create a doc node.  Requires coordinator or author role."""
    if not is_coordinator_or_author(caller):
        raise HTTPException(status_code=403, detail="coordinator_or_author_required")

    try:
        await pool.execute(
            """
            INSERT INTO docs_nodes (
                id, tier, entity_id, permitted_roles, project_id, squad_id,
                author_id, title, summary, body, body_format, frontmatter,
                version, supersedes
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            """,
            body.id,
            body.tier,
            body.entity_id,
            body.permitted_roles or [],
            body.project_id,
            body.squad_id,
            body.author_id,
            body.title,
            body.summary,
            body.body,
            body.body_format,
            body.frontmatter,
            body.version,
            body.supersedes,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="node_already_exists")
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid_reference: {exc}")

    _emit_event("sos:event:docs:node_created", {"node_id": body.id, "tier": body.tier})
    asyncio.create_task(
        emit_audit(
            AuditChainEvent(
                stream_id="kernel",
                actor_id=caller.entity_id or "system",
                actor_type="agent" if "agent" in (caller.roles or []) else "human",
                action="created",
                resource=f"doc_node:{body.id}",
                payload={
                    "node_id": body.id,
                    "tier": body.tier,
                    "title": body.title,
                    "project_id": body.project_id,
                },
            )
        )
    )
    return JSONResponse({"id": body.id, "status": "created"}, status_code=201)


@app.patch("/docs/nodes/{node_id}/tier")
async def patch_tier(
    node_id: str,
    body: TierPatch,
    caller: CallerContext = Depends(_resolve_caller),
    pool: asyncpg.Pool = Depends(get_pool),
) -> JSONResponse:
    """Promote or demote a node's tier.  Requires coordinator role."""
    if not is_coordinator(caller):
        raise HTTPException(status_code=403, detail="coordinator_required")

    result = await pool.execute(
        "UPDATE docs_nodes SET tier = $1 WHERE id = $2",
        body.tier,
        node_id,
    )
    # asyncpg returns "UPDATE N" string
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="not_found")

    _emit_event("sos:event:docs:node_promoted", {"node_id": node_id, "tier": body.tier})
    asyncio.create_task(
        emit_audit(
            AuditChainEvent(
                stream_id="kernel",
                actor_id=caller.entity_id or "system",
                actor_type="agent" if "agent" in (caller.roles or []) else "human",
                action="updated",
                resource=f"doc_node:{node_id}",
                payload={"node_id": node_id, "new_tier": body.tier},
            )
        )
    )
    return JSONResponse({"id": node_id, "tier": body.tier})


@app.post("/docs/nodes/{node_id}/relations", status_code=201)
async def add_relation(
    node_id: str,
    body: RelationCreate,
    caller: CallerContext = Depends(_resolve_caller),
    pool: asyncpg.Pool = Depends(get_pool),
) -> JSONResponse:
    """Add an edge from *node_id* to *body.to_node*.  Requires coordinator role."""
    if not is_coordinator(caller):
        raise HTTPException(status_code=403, detail="coordinator_required")

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO docs_relations (from_node, to_node, edge_type, weight)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            node_id,
            body.to_node,
            body.edge_type,
            body.weight,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="relation_already_exists")
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid_reference: {exc}")

    relation_id: int = row["id"]
    _emit_event(
        "sos:event:docs:relation_added",
        {"from_node": node_id, "to_node": body.to_node, "edge_type": body.edge_type},
    )
    asyncio.create_task(
        emit_audit(
            AuditChainEvent(
                stream_id="kernel",
                actor_id=caller.entity_id or "system",
                actor_type="agent" if "agent" in (caller.roles or []) else "human",
                action="created",
                resource=f"doc_relation:{relation_id}",
                payload={
                    "from_node": node_id,
                    "to_node": body.to_node,
                    "edge_type": body.edge_type,
                },
            )
        )
    )
    return JSONResponse({"id": relation_id, "status": "created"}, status_code=201)


# ---------------------------------------------------------------------------
# Fire-and-forget event emission
# ---------------------------------------------------------------------------


def _emit_event(event_type: str, payload: dict[str, Any]) -> None:
    """Emit a bus event.  Non-blocking; never raises — bus failures are not fatal."""
    import asyncio

    async def _fire() -> None:
        try:
            import httpx

            bus_url = os.getenv("SOS_BUS_URL", "http://localhost:6380")
            token = os.getenv("SOS_DOCS_TOKEN", "")
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(
                    f"{bus_url}/emit",
                    json={"type": event_type, "payload": payload},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except Exception:
            pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_fire())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("SOS_DOCS_PORT", "8085"))
    uvicorn.run("sos.services.docs.app:app", host="0.0.0.0", port=port, reload=False)
