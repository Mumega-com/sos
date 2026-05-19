"""
Content Orchestrator — The Autonomous Witness Gate

Automates the Oracle -> Architect loop for the Content Service.
Uses the Swarm Council for validation before publishing.
"""

import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from sos.kernel import Message, MessageType
from sos.kernel.bus import get_bus
from sos.contracts.engine import ChatRequest
from sos.services.content.calendar import ContentCalendar, PostStatus
from sos.services.content.publisher import get_publisher
from sos.services.content.strategy import ContentStrategy, MUMEGA_STRATEGY

logger = logging.getLogger("content.orchestrator")

class ContentOrchestrator:
    def __init__(self, engine_client, council):
        self.engine = engine_client
        self.council = council
        self.calendar = ContentCalendar()
        self.publisher = get_publisher()
        self.bus = get_bus()
        self.running = False

    async def start(self):
        """Start the autonomous orchestration loop."""
        self.running = True
        logger.info("🎬 Content Orchestrator started")
        
        while self.running:
            try:
                await self._orchestrate_step()
                await asyncio.sleep(300) # Check every 5 minutes
            except Exception as e:
                logger.error(f"Orchestration error: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _orchestrate_step(self):
        """Perform one step of the orchestration logic."""
        
        # 1. Find PLANNED posts that are due
        upcoming = self.calendar.get_upcoming(days=1)
        planned = [p for p in upcoming if p.status == PostStatus.PLANNED]
        
        for post in planned:
            logger.info(f"🔮 Processing PLANNED post: {post.title}")
            await self._run_generation_loop(post)

    async def _run_generation_loop(self, post):
        """Chains Oracle generation and Council proposal."""
        
        # 1. Oracle Phase: Generate Draft
        # Use River (Gemini) as the Oracle
        strategy = MUMEGA_STRATEGY # In production, load from config
        brief = strategy.generate_brief(post.pillar_id, post.format, post.target_audience)
        
        prompt = f"""Generate a content draft for: {post.title}
        
        Brief:
        {json.dumps(brief, indent=2)}
        
        Keywords to include: {', '.join(post.keywords)}
        
        Return markdown content only.
        """
        
        logger.info(f"✨ Requesting Oracle draft for: {post.title}")
        chat_request = ChatRequest(
            agent_id="agent:River",
            message=prompt,
            model="gemini-3-flash-preview",
            memory_enabled=True,
        )
        response = await self.engine.chat(chat_request)

        draft_content = response.content
        
        # 2. Update Calendar to IN_REVIEW
        self.calendar.update_post(post.id, status=PostStatus.IN_REVIEW, draft_content=draft_content)
        
        # 3. Superposition Phase: Submit Proposal to Council
        # This triggers the Architect phase (Athena/Kasra/Human)
        logger.info(f"⚖️ Submitting Council Proposal for: {post.title}")
        
        proposal_id = await self.council.propose(
            agent_id="agent:River",
            title=f"CONTENT_WITNESS: {post.title}",
            payload={
                "type": "content_approval",
                "post_id": post.id,
                "title": post.title,
                "content_preview": draft_content[:500],
                "slug": post.slug or post.title.lower().replace(" ", "-")
            }
        )
        
        # 4. Finality Logic: The Council will trigger the callback on approval
        # (This orchestrator is async; it doesn't block for the vote)
        logger.info(f"✅ Proposal {proposal_id} active. Waiting for Witness collapse.")

    async def handle_council_result(self, proposal_id: str, passed: bool, result_payload: Dict):
        """Callback triggered by the Swarm Council when a content proposal passes."""
        
        post_id = result_payload.get("post_id")
        if not post_id:
            return

        post = self.calendar.get_post(post_id)
        if not post:
            return

        if passed:
            logger.info(f"💎 Witness consensus reached for: {post.title}. Publishing.")
            
            # Publish to all destinations
            publish_results = await self.publisher.publish_all(
                slug=post.slug or post.title.lower().replace(" ", "-"),
                title=post.title,
                content=post.draft_content,
                destinations=["supabase"]
            )
            
            # Update Calendar to PUBLISHED
            self.calendar.update_post(post.id, status=PostStatus.PUBLISHED, final_content=post.draft_content)
            
            # Emit Success Signal to Redis
            await self.bus.publish("sos:content:published", Message(
                source="content_orchestrator",
                payload={"post_id": post.id, "url": publish_results.get("supabase", {}).get("url")}
            ))
        else:
            logger.warning(f"❌ Witness rejected content: {post.title}. Reason: {result_payload.get('reason')}")
            # Reset to PLANNED for retry or handle feedback
            self.calendar.update_post(post.id, status=PostStatus.PLANNED)

def get_orchestrator(engine, council):
    return ContentOrchestrator(engine, council)
