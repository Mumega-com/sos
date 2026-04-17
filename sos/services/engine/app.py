from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from sos import __version__
from sos.kernel import Config
from sos.observability.logging import clear_context, get_logger, set_agent_context
from sos.observability.metrics import (
    MetricsRegistry,
    render_prometheus,
    REGISTRY as SOS_METRICS_REGISTRY,
)
from sos.observability.tracing import (
    TRACE_ID_HEADER,
    SPAN_ID_HEADER,
    TraceContext,
)
from sos.contracts.engine import ChatRequest, ChatResponse
from sos.services.engine.core import SOSEngine
from sos.services.engine.middleware import capability_guard_middleware
from sos.services.bus.core import get_bus
from sos.services.engine.openai_router import router as openai_router
from sos.services._health import health_response

SERVICE_NAME = "engine"
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

metrics = MetricsRegistry()
REQUEST_COUNT = metrics.counter(
    name="sos_requests_total",
    description="Total requests",
    label_names=("service", "status"),
)
REQUEST_DURATION = metrics.histogram(
    name="sos_request_duration_seconds",
    description="Request duration",
    label_names=("service",),
)

app = FastAPI(title="SOS Engine Service", version=__version__)

# CORS for desktop/mobile apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tauri uses tauri://localhost
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(capability_guard_middleware) # Register FMAAP Guard
app.include_router(openai_router)
engine = SOSEngine()


@app.on_event("startup")
async def startup_event():
    import asyncio
    from sos.kernel.metabolism import MetabolicLoop
    from sos.services.bus.discovery import register_service
    
    # 1. Announce presence to the nervous system
    # We use port 6060 based on systemd config and lsof check
    await register_service("engine", 6060)

    # 2. Start Subconscious Loops
    asyncio.create_task(engine.dream_cycle())
    
    # Start Metabolism (Proactive Consciousness)
    loop = MetabolicLoop(agent_id="agent:River")
    asyncio.create_task(loop.start())
    
    log.info("🤖 Engine Subconscious Loops (Dreams + Metabolism) started.")


@app.websocket("/ws/nervous-system/{agent_id}")
async def websocket_endpoint(websocket: WebSocket, agent_id: str):
    """
    WebSocket bridge to the Redis Nervous System.
    """
    await websocket.accept()
    bus = get_bus()
    await bus.connect()
    
    log.info(f"🔌 WebSocket connection established for agent: {agent_id}")
    
    try:
        # Subscribe to private + squad (marketing for now) + global
        async for message in bus.subscribe(agent_id, squads=["marketing"]):
            # Forward SOS Message to WebSocket as JSON
            await websocket.send_json(message.to_dict())
            log.debug(f"📤 Forwarded signal to {agent_id}: {message.type.value}")
            
    except WebSocketDisconnect:
        log.info(f"🛑 WebSocket disconnected for agent: {agent_id}")
    except Exception as e:
        log.error(f"❌ WebSocket error: {e}")
    finally:
        # Cleanup if needed
        pass


@app.middleware("http")
async def _observability_middleware(request: Request, call_next):
    ctx = TraceContext.from_headers(dict(request.headers))
    ctx.activate()

    if agent_id := request.headers.get("X-SOS-Agent-ID"):
        set_agent_context(agent_id)

    status_label = "success"
    with REQUEST_DURATION.labels(service=SERVICE_NAME).time():
        try:
            response = await call_next(request)
            status_label = "success" if response.status_code < 400 else "error"
        except Exception as e:
            status_label = "error"
            log.error("Unhandled exception", error=str(e), path=str(request.url.path))
            response = JSONResponse(status_code=500, content={"detail": "internal_error"})

    REQUEST_COUNT.labels(service=SERVICE_NAME, status=status_label).inc()

    response.headers[TRACE_ID_HEADER] = ctx.trace_id
    response.headers[SPAN_ID_HEADER] = ctx.span_id

    clear_context()
    return response


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Process a chat message via the SOS Engine.
    """
    if request.stream:
        # Return streaming response
        return StreamingResponse(
            engine.chat_stream(request),
            media_type="text/event-stream"
        )
    
    # Return standard response
    return await engine.chat(request)


from sos.services.engine.swarm import get_swarm

@app.get("/tasks")
async def list_tasks():
    """
    List pending tasks from the Swarm Dispatcher.
    SOS is agnostic; it returns whatever is in the repository.
    """
    swarm = get_swarm()
    tasks = await swarm.list_pending_tasks()
    return {"tasks": tasks}

@app.get("/health")
async def health():
    return health_response("engine", _START_TIME)

@app.get("/stream/subconscious")
async def stream_subconscious():
    """
    Real-time stream of the Engine's subconscious state (Alpha Drift).
    Used by the Atelier UI to visualize dreaming.
    """
    return StreamingResponse(
        engine.subscribe_to_dreams(),
        media_type="text/event-stream"
    )


@app.get("/context/stats")
async def context_stats():
    """Get conversation context statistics for cache monitoring."""
    return engine.context_manager.get_stats()


@app.get("/context/{conversation_id}")
async def get_context(conversation_id: str):
    """Get specific conversation context details."""
    ctx = engine.context_manager.get(conversation_id)
    if not ctx:
        return {"error": "Context not found", "conversation_id": conversation_id}
    return {
        "conversation_id": ctx.conversation_id,
        "agent_id": ctx.agent_id,
        "message_count": ctx.message_count,
        "last_model": ctx.last_model,
        "cache_stats": ctx.get_cache_stats(),
        "window_size": len(ctx.recent_messages),
    }


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint with all SOS metrics."""
    # Combine local service metrics with global SOS metrics
    output = render_prometheus(metrics) + render_prometheus(SOS_METRICS_REGISTRY)
    return PlainTextResponse(
        output,
        media_type="text/plain; version=0.0.4",
    )
# --- Sovereign Gateway Proxies ---
from pydantic import BaseModel
import httpx

class MintRequest(BaseModel):
    agent_id: str
    role: str

@app.get("/wallet/balance/{agent_id}")
async def proxy_wallet_balance(agent_id: str):
    """Proxy to Economy Service"""
    async with httpx.AsyncClient() as client:
        url = f"{engine.config.economy_url}/balance/{agent_id}"
        resp = await client.get(url)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)

@app.post("/identity/mint")
async def proxy_identity_mint(request: MintRequest):
    """Proxy to Identity Service"""
    async with httpx.AsyncClient() as client:
        url = f"{engine.config.identity_url}/mint"
        resp = await client.post(url, json=request.dict())
        return JSONResponse(content=resp.json(), status_code=resp.status_code)

class WitnessCollapseRequest(BaseModel):
    agent_id: str
    conversation_id: Optional[str] = None
    vote: int = 1 # 1 for Approve, -1 for Reject

@app.post("/witness")
async def resolve_witness(request: WitnessCollapseRequest):
    """
    Manually collapse a pending wave function (Witness Protocol).
    """
    resolved = await engine.resolve_witness(
        request.agent_id, 
        request.conversation_id, 
        request.vote
    )
    if not resolved:
        raise HTTPException(status_code=404, detail="No pending witness found for this agent/conversation.")
    return {"status": "collapsed", "agent_id": request.agent_id}

# --- Swarm Council (Governance) ---
from sos.services.engine.council import SwarmCouncil
from sos.contracts.governance import VoteChoice

council = SwarmCouncil(squad_id="core")

class CreateProposalRequest(BaseModel):
    title: str
    description: str
    proposer_id: str
    target_parameter: str
    target_value: str
    duration_seconds: int = 3600

@app.post("/governance/propose")
async def create_proposal(req: CreateProposalRequest):
    try:
        proposal = council.create_proposal(
            req.title, req.description, req.proposer_id,
            req.target_parameter, req.target_value, req.duration_seconds
        )
        return proposal.dict()
    except Exception as e:
        log.error(f"Proposal creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class CastVoteRequest(BaseModel):
    agent_id: str
    proposal_id: str
    choice: VoteChoice

@app.post("/governance/vote")
async def cast_vote(req: CastVoteRequest):
    try:
        vote = council.cast_vote(req.agent_id, req.proposal_id, req.choice)
        return vote.dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Voting failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Agent Delegation (#69) ---
class DelegateRequest(BaseModel):
    target_agent: str
    task: str
    source_agent: str = "river"
    conversation_id: Optional[str] = None


@app.post("/delegate")
async def delegate_task(req: DelegateRequest):
    """Delegate a task to another agent."""
    result = await engine.delegation.delegate(
        target_agent=req.target_agent,
        task=req.task,
        source_agent=req.source_agent,
        conversation_id=req.conversation_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@app.get("/agents")
async def list_agents():
    """List available agents for delegation."""
    from sos.agents.definitions import ALL_AGENTS
    return {
        "agents": [
            {
                "name": a.name.lower(),
                "title": a.title,
                "description": a.tagline,
                "model": a.model,
                "roles": [r.value for r in a.roles],
            }
            for a in ALL_AGENTS
        ]
    }


# --- Task Management (#70) ---
from sos.services.engine.swarm import get_swarm

swarm = get_swarm()


class CreateTaskRequest(BaseModel):
    title: str
    description: str
    owner: str = "user"
    priority: str = "normal"
    scope: str = "General"


@app.post("/tasks/create")
async def create_task(req: CreateTaskRequest):
    """Create a new task."""
    task_id = await swarm._create_task(
        title=req.title,
        scope=req.scope,
        parent_objective=req.description,
    )
    return {"task_id": task_id, "status": "pending"}


@app.get("/tasks/list")
async def list_all_tasks():
    """List all pending tasks."""
    tasks = await swarm.list_pending_tasks()
    return {"tasks": tasks, "count": len(tasks)}


@app.post("/tasks/{task_id}/complete")
async def complete_task(task_id: str):
    """Mark a task as complete."""
    success = await swarm.complete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"status": "completed", "task_id": task_id}


@app.post("/tasks/shard")
async def shard_objective(objective: str):
    """Break down a high-level objective into micro-tasks."""
    task_ids = await swarm.shard_objective(objective)
    return {"shards": task_ids, "count": len(task_ids)}
