"""
SOS Content Service API

FastAPI service for content management, strategy, and publishing.
Exposes HTTP APIs for the dashboard and other clients.
"""

from __future__ import annotations

import os
import time
import asyncio
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from sos import __version__
from sos.observability.logging import get_logger, clear_context, set_agent_context
from sos.observability.metrics import MetricsRegistry, render_prometheus
from sos.observability.tracing import TraceContext, TRACE_ID_HEADER, SPAN_ID_HEADER

from sos.services.content.strategy import ContentStrategy, MUMEGA_STRATEGY
from sos.services.content.calendar import ContentCalendar, ScheduledPost, PostStatus
from sos.services.content.publisher import ContentPublisher, get_publisher
from sos.services.content.orchestrator import ContentOrchestrator

SERVICE_NAME = "content"
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

metrics = MetricsRegistry()
REQUEST_COUNT = metrics.counter(
    name="sos_content_requests_total",
    description="Total content service requests",
    label_names=("endpoint", "status"),
)

app = FastAPI(title="SOS Content Service", version=__version__)

# Initialize services
_calendar = ContentCalendar()
_publisher = get_publisher()
_strategy_path = Path.home() / ".sos" / "content" / "mumega_strategy.yaml"

# Global orchestrator instance
_orchestrator: Optional[ContentOrchestrator] = None


# --- Pydantic Models ---

class StrategyResponse(BaseModel):
    brand_voice: str
    mission: str
    vision: str
    posts_per_week: int
    pillars: List[Dict[str, Any]]
    audiences: List[Dict[str, Any]]
    seo_keywords: List[str]
    competitors: List[str]


class CalendarEntryCreate(BaseModel):
    title: str
    pillar_id: str
    format: str
    target_audience: str
    scheduled_date: str  # ISO format
    keywords: Optional[List[str]] = None


class CalendarEntryUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    draft_content: Optional[str] = None
    final_content: Optional[str] = None
    slug: Optional[str] = None


class PublishRequest(BaseModel):
    slug: str
    title: str
    content: str
    metadata: Optional[Dict[str, Any]] = None
    destinations: Optional[List[str]] = None  # ["supabase", "file", "social"]


class ApprovalRequest(BaseModel):
    action: str  # "approve" or "reject"
    reason: Optional[str] = None


# --- Lifecycle ---

@app.on_event("startup")
async def startup_event():
    global _orchestrator
    
    log.info("🚀 Starting Content Service Subconscious...")

    # 1. Initialize Clients
    from sos.clients.engine import AsyncEngineClient
    from sos.kernel.council import create_council

    engine = AsyncEngineClient(
        base_url=os.environ.get("SOS_ENGINE_URL", "http://localhost:6060")
    )
    council = create_council(squad_id="marketing")
    
    # 2. Start Orchestrator
    _orchestrator = ContentOrchestrator(engine, council)
    asyncio.create_task(_orchestrator.start())
    
    # 3. Start Council Listener in a background task
    # We iterate over the async generator from bus.subscribe
    asyncio.create_task(run_council_listener(_orchestrator))
    
    log.info("✅ Content Orchestrator & Witness Loop active.")

async def run_council_listener(orchestrator: ContentOrchestrator):
    """Background task to listen for Council results."""
    from sos.kernel.bus import get_bus
    bus = get_bus()
    await bus.connect()
    
    log.info("🎧 Listening for Swarm Council events on squad:marketing...")
    
    try:
        # Iterate over the async generator
        async for msg in bus.subscribe("content_service", squads=["marketing"]):
            # Note: agent_id 'content_service' is used for the subscription
            payload = msg.payload
            event = payload.get("event")
            
            if event == "PROPOSAL_RESULT":
                proposal_id = payload.get("proposal_id", "")
                if proposal_id.startswith("CONTENT_WITNESS"):
                    passed = payload.get("status") == "PASSED"
                    await orchestrator.handle_council_result(proposal_id, passed, payload)
    except Exception as e:
        log.error(f"Council listener error: {e}", exc_info=True)


# --- Middleware ---

@app.middleware("http")
async def _observability_middleware(request: Request, call_next):
    ctx = TraceContext.from_headers(dict(request.headers))
    ctx.activate()

    if agent_id := request.headers.get("X-SOS-Agent-ID"):
        set_agent_context(agent_id)

    status_label = "success"
    try:
        response = await call_next(request)
        status_label = "success" if response.status_code < 400 else "error"
    except Exception as e:
        status_label = "error"
        log.error("Unhandled exception", error=str(e), path=str(request.url.path))
        response = JSONResponse(status_code=500, content={"detail": "internal_error"})

    REQUEST_COUNT.labels(endpoint=str(request.url.path), status=status_label).inc()

    response.headers[TRACE_ID_HEADER] = ctx.trace_id
    response.headers[SPAN_ID_HEADER] = ctx.span_id

    clear_context()
    return response


# --- Health & Metrics ---

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "service": SERVICE_NAME,
        "uptime_seconds": time.time() - _START_TIME,
        "calendar_posts": len(_calendar.posts),
        "strategy_loaded": _strategy_path.exists(),
        "orchestrator_active": _orchestrator.running if _orchestrator else False
    }


@app.get("/metrics")
async def metrics_endpoint():
    return PlainTextResponse(
        render_prometheus(metrics),
        media_type="text/plain; version=0.0.4",
    )


# --- Strategy Endpoints ---

@app.get("/strategy", response_model=StrategyResponse)
async def get_strategy():
    """Get current content strategy"""
    if _strategy_path.exists():
        strategy = ContentStrategy.load(_strategy_path)
    else:
        strategy = MUMEGA_STRATEGY

    return {
        "brand_voice": strategy.brand_voice,
        "mission": strategy.mission,
        "vision": strategy.vision,
        "posts_per_week": strategy.posts_per_week,
        "pillars": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "keywords": p.keywords,
                "formats": [f.value for f in p.formats],
                "audiences": p.target_audiences,
                "examples": p.examples,
            }
            for p in strategy.pillars
        ],
        "audiences": [
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "pain_points": a.pain_points,
                "goals": a.goals,
                "channels": a.channels,
                "tone": a.tone,
            }
            for a in strategy.audiences
        ],
        "seo_keywords": strategy.seo_keywords,
        "competitors": strategy.competitors,
    }


@app.put("/strategy")
async def update_strategy(data: Dict[str, Any]):
    """Update content strategy"""
    if _strategy_path.exists():
        strategy = ContentStrategy.load(_strategy_path)
    else:
        strategy = MUMEGA_STRATEGY

    # Update fields
    if "brand_voice" in data:
        strategy.brand_voice = data["brand_voice"]
    if "mission" in data:
        strategy.mission = data["mission"]
    if "vision" in data:
        strategy.vision = data["vision"]
    if "posts_per_week" in data:
        strategy.posts_per_week = data["posts_per_week"]
    if "seo_keywords" in data:
        strategy.seo_keywords = data["seo_keywords"]
    if "competitors" in data:
        strategy.competitors = data["competitors"]

    # Save
    _strategy_path.parent.mkdir(parents=True, exist_ok=True)
    strategy.save(_strategy_path)

    log.info("Strategy updated", path=str(_strategy_path))
    return {"success": True, "message": "Strategy updated"}


# --- Calendar Endpoints ---

@app.get("/calendar")
async def get_calendar(weeks: int = 4):
    """Get calendar view"""
    return {
        "calendar": _calendar.get_calendar_view(weeks),
        "stats": _calendar.get_stats(),
    }


@app.get("/calendar/upcoming")
async def get_upcoming(days: int = 7):
    """Get upcoming posts"""
    posts = _calendar.get_upcoming(days)
    return {"posts": [p.to_dict() for p in posts]}


@app.get("/calendar/queue")
async def get_approval_queue():
    """Get posts needing approval (drafts ready for review)"""
    drafting = _calendar.get_by_status(PostStatus.DRAFTING)
    in_review = _calendar.get_by_status(PostStatus.IN_REVIEW)
    return {
        "drafting": [p.to_dict() for p in drafting],
        "in_review": [p.to_dict() for p in in_review],
        "total": len(drafting) + len(in_review),
    }


@app.post("/calendar/posts")
async def create_calendar_post(entry: CalendarEntryCreate):
    """Create a new calendar post"""
    from datetime import datetime

    post = _calendar.create_post(
        title=entry.title,
        pillar_id=entry.pillar_id,
        format=entry.format,
        target_audience=entry.target_audience,
        scheduled_date=datetime.fromisoformat(entry.scheduled_date),
        keywords=entry.keywords or [],
    )

    log.info("Calendar post created", post_id=post.id, title=post.title)
    return {"success": True, "post": post.to_dict()}


@app.get("/calendar/posts/{post_id}")
async def get_calendar_post(post_id: str):
    """Get a specific post"""
    post = _calendar.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post.to_dict()


@app.patch("/calendar/posts/{post_id}")
async def update_calendar_post(post_id: str, updates: CalendarEntryUpdate):
    """Update a calendar post"""
    update_dict = {k: v for k, v in updates.dict().items() if v is not None}

    if "status" in update_dict:
        update_dict["status"] = PostStatus(update_dict["status"])

    post = _calendar.update_post(post_id, **update_dict)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    log.info("Calendar post updated", post_id=post_id)
    return {"success": True, "post": post.to_dict()}


@app.post("/calendar/posts/{post_id}/approve")
async def approve_post(post_id: str, request: ApprovalRequest):
    """Approve or reject a post"""
    post = _calendar.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if request.action == "approve":
        _calendar.update_status(post_id, PostStatus.APPROVED)
        log.info("Post approved", post_id=post_id)
        return {"success": True, "status": "approved"}
    elif request.action == "reject":
        _calendar.update_status(post_id, PostStatus.PLANNED)
        log.info("Post rejected", post_id=post_id, reason=request.reason)
        return {"success": True, "status": "rejected", "reason": request.reason}
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


@app.post("/calendar/generate-week")
async def generate_week_plan(posts_count: int = 3):
    """Auto-generate a week's content plan"""
    if _strategy_path.exists():
        strategy = ContentStrategy.load(_strategy_path)
    else:
        strategy = MUMEGA_STRATEGY

    posts = _calendar.generate_week_plan(strategy, posts_count=posts_count)
    log.info("Week plan generated", posts_count=len(posts))
    return {
        "success": True,
        "posts": [p.to_dict() for p in posts],
    }


# --- Publishing Endpoints ---

@app.post("/publish")
async def publish_content(request: PublishRequest):
    """Publish content to destinations"""
    destinations = request.destinations or ["supabase"]

    results = await _publisher.publish_all(
        slug=request.slug,
        title=request.title,
        content=request.content,
        metadata=request.metadata,
        destinations=destinations,
    )

    success = all(r.success for r in results.values())
    log.info(
        "Content published",
        slug=request.slug,
        destinations=destinations,
        success=success,
    )

    return {
        "success": success,
        "results": {
            dest: {
                "success": r.success,
                "url": r.url,
                "error": r.error,
            }
            for dest, r in results.items()
        },
    }


@app.get("/stats")
async def get_content_stats():
    """Get overall content statistics"""
    calendar_stats = _calendar.get_stats()

    return {
        "calendar": calendar_stats,
        "strategy": {
            "pillars": len(MUMEGA_STRATEGY.pillars),
            "audiences": len(MUMEGA_STRATEGY.audiences),
            "seo_keywords": len(MUMEGA_STRATEGY.seo_keywords),
        },
    }
