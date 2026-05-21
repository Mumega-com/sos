"""Generic MCP transport helpers for the public SOS kernel."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


AuthHook = Callable[[Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


class RegistryProtocol(Protocol):
    def list_tools(self) -> list[dict[str, Any]]:
        ...

    async def dispatch(self, name: str, arguments: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        ...


@dataclass
class SessionStore:
    queues: dict[str, asyncio.Queue[dict[str, Any]]] = field(default_factory=dict)

    def open(self) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
        session_id = uuid4().hex
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.queues[session_id] = queue
        return session_id, queue

    def get(self, session_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        return self.queues.get(session_id)

    def close(self, session_id: str) -> None:
        self.queues.pop(session_id, None)


def jsonrpc_ok(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def jsonrpc_error(msg_id: Any, message: str, code: int = -32000) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def resolve_auth(headers: Mapping[str, Any], hook: AuthHook | None = None) -> Mapping[str, Any]:
    if hook is None:
        return {}
    result = hook(headers)
    if hasattr(result, "__await__"):
        result = await result  # type: ignore[assignment]
    return dict(result)


async def process_jsonrpc(body: Mapping[str, Any], registry: RegistryProtocol) -> dict[str, Any] | None:
    method = str(body.get("method", ""))
    msg_id = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return jsonrpc_ok(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "sos", "version": "public-kernel"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return jsonrpc_ok(msg_id, {"tools": registry.list_tools()})
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        try:
            result = await registry.dispatch(name, arguments)
        except Exception as exc:
            return jsonrpc_error(msg_id, str(exc))
        return jsonrpc_ok(msg_id, result)
    if method == "ping":
        return jsonrpc_ok(msg_id, {})
    return jsonrpc_error(msg_id, f"Unknown method: {method}", code=-32601)


async def iter_sse(queue: asyncio.Queue[dict[str, Any]]) -> AsyncIterator[str]:
    while True:
        event = await queue.get()
        yield f"data: {json.dumps(event)}\n\n"


def create_transport_app(registry: RegistryProtocol, auth_hook: AuthHook | None = None) -> Any:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import StreamingResponse

    app = FastAPI(title="SOS MCP Transport")
    sessions = SessionStore()

    @app.get("/sse")
    async def sse(authorization: str | None = Header(default=None)) -> StreamingResponse:
        await resolve_auth({"authorization": authorization or ""}, auth_hook)
        session_id, queue = sessions.open()
        await queue.put({"jsonrpc": "2.0", "method": "sos/session", "params": {"id": session_id}})
        return StreamingResponse(iter_sse(queue), media_type="text/event-stream")

    @app.post("/messages")
    async def messages(request: Request) -> dict[str, Any]:
        body = await request.json()
        response = await process_jsonrpc(body, registry)
        if response is None:
            return {"status": "ok"}
        return response

    @app.post("/sessions/{session_id}/messages")
    async def session_messages(session_id: str, request: Request) -> dict[str, Any]:
        queue = sessions.get(session_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="session not found")
        body = await request.json()
        response = await process_jsonrpc(body, registry)
        if response is not None:
            await queue.put(response)
        return {"status": "queued"}

    return app
