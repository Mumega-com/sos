"""SOS Dispatcher — Python reference implementation.

Runs on any Linux host. Proxies MCP SSE + streamable-HTTP requests to the SOS
MCP gateway at localhost:6070, with token validation + rate limiting + request
logging at the edge.

Deploy: `python -m sos.services.dispatcher` or via systemd unit.
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from sos.services.dispatcher.auth import AuthContext, identity_headers, resolve_token
from sos.services.dispatcher.rate_limit import check_rate_limit
from sos.services.dispatcher.request_log import log_request


UPSTREAM_BASE = os.environ.get("SOS_DISPATCHER_UPSTREAM", "http://127.0.0.1:6070")
PORT = int(os.environ.get("SOS_DISPATCHER_PORT", "6071"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    timeout = httpx.Timeout(connect=2.0, read=60.0, write=30.0, pool=5.0)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, base_url=UPSTREAM_BASE) as client:
        app.state.upstream = client
        yield


app = FastAPI(title="SOS Dispatcher (Python)", lifespan=lifespan, docs_url=None, redoc_url=None)


# ── Error helpers ─────────────────────────────────────────────────────────────

def _err(status: int, code: str, message: str, extra: Optional[dict] = None) -> JSONResponse:
    body = {"code": code, "message": message}
    if extra:
        body.update(extra)
    headers = {}
    if code == "SOS-9003" and extra and "retry_after" in extra:
        headers["Retry-After"] = str(extra["retry_after"])
    return JSONResponse(body, status_code=status, headers=headers)


# ── Auth + rate-limit pipeline ────────────────────────────────────────────────

async def _gate(token: str, tenant_id_hint: Optional[str] = None) -> tuple[AuthContext, Optional[JSONResponse]]:
    ctx = resolve_token(token)
    if not ctx:
        return None, _err(401, "SOS-1001", "invalid token")  # type: ignore[return-value]

    decision = check_rate_limit(ctx.tenant_id, ctx.plan)
    if not decision.allowed:
        return ctx, _err(
            429, "SOS-9003", "rate limit exceeded",
            {"retry_after": decision.retry_after_s},
        )

    return ctx, None


# ── Handlers ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "service": "sos-dispatcher",
        "source": "dispatcher-py",
        "upstream": UPSTREAM_BASE,
    })


@app.get("/sse/{token}")
async def sse_proxy(token: str, request: Request) -> Response:
    start = time.monotonic()
    ctx, err = await _gate(token)
    if err:
        await log_request(
            tenant_id=None, agent="unknown", scope="unknown",
            endpoint="/sse", method="GET",
            status=err.status_code, latency_ms=int((time.monotonic() - start) * 1000),
            error_code=err.body and None or None,
        )
        return err

    upstream: httpx.AsyncClient = request.app.state.upstream
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.update(identity_headers(ctx))

    async def _stream() -> AsyncIterator[bytes]:
        async with upstream.stream("GET", f"/sse/{token}", headers=headers, timeout=None) as resp:
            async for chunk in resp.aiter_raw():
                yield chunk

    # Note: we log at stream-start; byte-level logging would require wrapping the iterator
    await log_request(
        tenant_id=ctx.tenant_id, agent=ctx.agent, scope=ctx.scope,
        endpoint="/sse", method="GET", status=200,
        latency_ms=int((time.monotonic() - start) * 1000),
    )

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/messages")
async def messages_proxy(request: Request) -> Response:
    # /messages endpoint doesn't carry token in URL — trusts session_id from /sse/<token> earlier
    # Forward verbatim; upstream has its own session registry.
    start = time.monotonic()
    upstream: httpx.AsyncClient = request.app.state.upstream
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    headers["X-SOS-Source"] = "dispatcher-py"

    resp = await upstream.post(
        f"/messages?{request.url.query}",
        content=body,
        headers=headers,
    )

    await log_request(
        tenant_id=None, agent="session", scope="session",
        endpoint="/messages", method="POST",
        status=resp.status_code,
        latency_ms=int((time.monotonic() - start) * 1000),
        bytes_out=len(resp.content),
    )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in ("transfer-encoding", "content-encoding")},
    )


@app.post("/mcp/{token}")
async def mcp_streamable(token: str, request: Request) -> Response:
    start = time.monotonic()
    ctx, err = await _gate(token)
    if err:
        await log_request(
            tenant_id=None, agent="unknown", scope="unknown",
            endpoint="/mcp", method="POST",
            status=err.status_code, latency_ms=int((time.monotonic() - start) * 1000),
        )
        return err

    upstream: httpx.AsyncClient = request.app.state.upstream
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.update(identity_headers(ctx))

    resp = await upstream.post(f"/mcp/{token}", content=body, headers=headers)

    await log_request(
        tenant_id=ctx.tenant_id, agent=ctx.agent, scope=ctx.scope,
        endpoint="/mcp", method="POST",
        status=resp.status_code,
        latency_ms=int((time.monotonic() - start) * 1000),
        bytes_out=len(resp.content),
    )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in ("transfer-encoding", "content-encoding")},
    )
