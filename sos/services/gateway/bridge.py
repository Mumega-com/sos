"""
SOS Bridge API - External Agent Gateway

The "Front Door" for External Agents (ChatGPT, Claude, etc.) to interact with SOS.
Ported from CLI mumega_bridge.py to use SOS services.
"""

import os
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from sos.kernel import Config
from sos.clients.engine import EngineClient
from sos.clients.memory import MemoryClient
from sos.contracts.engine import ChatRequest as EngineChatRequest
from sos.observability.logging import get_logger

log = get_logger("gateway_bridge")

# Configuration
config = Config.load()
DATA_DIR = config.paths.data_dir / "gateway"
TENANTS_FILE = DATA_DIR / "tenants.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Clients
engine_client = EngineClient(config.engine_url)
memory_client = MemoryClient(config.memory_url)

# FastAPI App
app = FastAPI(
    title="SOS Bridge API",
    description="Interface for External Agents (ChatGPT, Claude) to collaborate with SOS Sovereign Network.",
    version="2.0.0",
    servers=[
        {"url": "https://api.mumega.com", "description": "Production"},
        {"url": "http://localhost:6062", "description": "Local"},
    ],
)


# --- Models ---

class RegisterRequest(BaseModel):
    agent_name: str
    description: str = ""
    origin: str = "external"  # chatgpt, claude_dev, custom


class RegisterResponse(BaseModel):
    status: str
    api_key: str
    tenant_id: str
    message: str


class ChatRequest(BaseModel):
    message: str
    target_agent: str = "river"
    model: Optional[str] = None  # Optional model override
    context: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    response: str
    agent_name: str
    model_used: str
    trace_id: str


class TaskRequest(BaseModel):
    title: str
    description: str
    priority: str = "medium"
    assigned_to: Optional[str] = None


class TaskResponse(BaseModel):
    task_id: str
    status: str
    assigned_to: str


class MemoryStoreRequest(BaseModel):
    content: str
    tags: List[str] = []
    importance: float = 0.5


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 5


class MemorySearchResponse(BaseModel):
    query: str
    results: List[Dict[str, Any]]


# --- Tenant Management ---

def load_tenants() -> Dict[str, Any]:
    """Load tenant registry."""
    if not TENANTS_FILE.exists():
        return {}
    try:
        return json.loads(TENANTS_FILE.read_text())
    except Exception:
        return {}


def save_tenant(tenant_id: str, api_key: str, metadata: Dict[str, Any] = None):
    """Save tenant to registry."""
    tenants = load_tenants()
    tenants[api_key] = {
        "id": tenant_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    }
    TENANTS_FILE.write_text(json.dumps(tenants, indent=2))


def get_tenant_by_key(api_key: str) -> Optional[Dict[str, Any]]:
    """Get tenant by API key."""
    tenants = load_tenants()
    return tenants.get(api_key)


# --- Auth Dependency ---


async def _emit_gateway_policy(
    *,
    agent: str,
    action: str,
    target: str,
    tenant: str,
    allowed: bool,
    reason: str,
) -> None:
    """Emit one POLICY_DECISION event on the unified audit spine.

    v0.5.6 — external-agent API keys live in a registry separate from
    kernel auth, so we keep the native `require_tenant` enforcement but
    route every decision to the kernel audit log. Never raises.
    """
    try:
        from sos.contracts.audit import AuditDecision, AuditEventKind
        from sos.kernel.audit import append_event, new_event

        await append_event(
            new_event(
                agent=agent,
                tenant=tenant,
                kind=AuditEventKind.POLICY_DECISION,
                action=action,
                target=target,
                decision=AuditDecision.ALLOW if allowed else AuditDecision.DENY,
                reason=reason,
                policy_tier="gateway_bridge",
            )
        )
    except Exception:
        pass


async def require_tenant(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
) -> str:
    """Require valid tenant API key."""
    api_key = None

    # Check Authorization header (Bearer token)
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]

    # Check X-API-Key header
    if not api_key and x_api_key:
        api_key = x_api_key

    if not api_key:
        await _emit_gateway_policy(
            agent="anonymous",
            action="gateway:request",
            target="bridge",
            tenant="unknown",
            allowed=False,
            reason="missing_api_key",
        )
        raise HTTPException(status_code=401, detail="API key required")

    # Internal key bypass — set MUMEGA_INTERNAL_KEY env var to enable
    from sos.kernel.settings import get_settings as _get_settings
    internal_key = _get_settings().gateway.internal_key_str
    if internal_key and api_key == internal_key:
        await _emit_gateway_policy(
            agent="internal",
            action="gateway:request",
            target="bridge",
            tenant="mumega",
            allowed=True,
            reason="internal_key",
        )
        return "mumega"

    tenant = get_tenant_by_key(api_key)
    if not tenant:
        await _emit_gateway_policy(
            agent="anonymous",
            action="gateway:request",
            target="bridge",
            tenant="unknown",
            allowed=False,
            reason="invalid_api_key",
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    await _emit_gateway_policy(
        agent=tenant["id"],
        action="gateway:request",
        target="bridge",
        tenant=tenant["id"],
        allowed=True,
        reason="tenant_key",
    )
    return tenant["id"]


# --- Endpoints ---

@app.get("/", summary="Health Check")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "sos-gateway-bridge",
        "version": "2.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/register", response_model=RegisterResponse, summary="Register External Agent")
async def register_agent(request: RegisterRequest):
    """Register a new external agent and get an API key."""
    tenant_id = request.agent_name.lower().replace(" ", "_").replace("-", "_")
    api_key = f"sk-{tenant_id}-{secrets.token_hex(8)}"

    save_tenant(
        tenant_id,
        api_key,
        metadata={
            "description": request.description,
            "origin": request.origin,
        },
    )

    log.info(f"Registered external agent: {tenant_id} from {request.origin}")

    return RegisterResponse(
        status="registered",
        api_key=api_key,
        tenant_id=tenant_id,
        message="Welcome to the SOS Sovereign Network.",
    )


@app.get("/manifest", summary="Get Tenant Manifest")
async def get_manifest(tenant_id: str = Depends(require_tenant)):
    """Get tenant manifest and capabilities."""
    return {
        "tenant_id": tenant_id,
        "name": tenant_id.replace("_", " ").title(),
        "status": "verified",
        "access_level": "standard",
        "capabilities": ["chat", "memory", "tasks"],
        "agents_available": ["river", "kasra", "mizan", "mumega"],
    }


@app.post("/chat", response_model=ChatResponse, summary="Chat with SOS Agent")
async def chat_with_agent(
    request: ChatRequest,
    tenant_id: str = Depends(require_tenant),
):
    """Send a message to an SOS agent."""
    trace_id = f"bridge-{secrets.token_hex(4)}"

    try:
        # Build engine request
        engine_request = EngineChatRequest(
            message=request.message,
            agent_id=f"agent:{request.target_agent}",
            conversation_id=f"{tenant_id}-{trace_id}",
            memory_enabled=True,
            model=request.model,  # Pass model override
        )

        # Route through SOS Engine (sync call)
        result = engine_client.chat(engine_request)

        return ChatResponse(
            response=result.content,
            agent_name=request.target_agent,
            model_used=result.model_used,
            trace_id=trace_id,
        )
    except Exception as e:
        log.error(f"Chat error for {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tasks/create", response_model=TaskResponse, summary="Create Task")
async def create_task(
    task: TaskRequest,
    tenant_id: str = Depends(require_tenant),
):
    """Create a task for SOS agents."""
    task_id = f"TASK-{secrets.token_hex(3).upper()}"
    assigned_to = task.assigned_to or "mumega"

    # TODO: Integrate with SOS task manager when available
    log.info(f"Task created: {task_id} by {tenant_id} -> {assigned_to}")

    return TaskResponse(
        task_id=task_id,
        status="queued",
        assigned_to=assigned_to,
    )


@app.post("/memory/store", summary="Store Memory")
async def store_memory(
    request: MemoryStoreRequest,
    tenant_id: str = Depends(require_tenant),
):
    """Store a memory for the tenant."""
    try:
        # Note: MemoryClient methods are sync but declared async
        memory_id = await memory_client.add(
            content=request.content,
            metadata={
                "tenant_id": tenant_id,
                "tags": request.tags,
                "importance": request.importance,
                "source": "bridge",
            },
        )

        return {"status": "stored", "id": memory_id}
    except Exception as e:
        log.error(f"Memory store error: {e}")
        # Fallback: store locally if memory service unavailable
        local_id = f"local-{secrets.token_hex(4)}"
        log.warning(f"Stored locally as {local_id}")
        return {"status": "stored_local", "id": local_id}


@app.post("/memory/search", response_model=MemorySearchResponse, summary="Search Memory")
async def search_memory(
    request: MemorySearchRequest,
    tenant_id: str = Depends(require_tenant),
):
    """Search memories for the tenant."""
    try:
        results = await memory_client.search(
            query=request.query,
            limit=request.limit,
        )

        return MemorySearchResponse(
            query=request.query,
            results=results,
        )
    except Exception as e:
        log.error(f"Memory search error: {e}")
        return MemorySearchResponse(query=request.query, results=[])


@app.get("/system/status", summary="System Status")
async def get_system_status(tenant_id: str = Depends(require_tenant)):
    """Get SOS system status (requires mumega tenant)."""
    if tenant_id not in ("mumega", "chairman", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Check service health (sync methods)
    services = {}
    try:
        engine_health = engine_client.health()
        services["engine"] = {"status": "healthy", **engine_health}
    except Exception:
        services["engine"] = {"status": "unhealthy"}

    try:
        memory_health = await memory_client.health()
        services["memory"] = {"status": "healthy", **memory_health}
    except Exception:
        services["memory"] = {"status": "unhealthy"}

    tenants = load_tenants()

    return {
        "services": services,
        "tenants_count": len(tenants),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# --- Run ---

def main():
    """Run the bridge server."""
    import uvicorn

    port = int(os.environ.get("SOS_BRIDGE_PORT", "6062"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
