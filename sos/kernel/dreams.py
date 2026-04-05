"""
Dream Synthesizer - LLM-powered insight extraction for SOS agents.

Uses LLMs to synthesize insights from memories during idle time.
Part of the SOS Kernel for agent autonomy.

Dream Types:
- pattern_synthesis: Identify recurring patterns across conversations
- insight_extraction: Extract key insights and learnings
- emotional_landscape: Map emotional themes and tones
- topic_clustering: Group conversations by topic/theme
- connection_finding: Find unexpected connections between topics

Usage:
    from sos.kernel.dreams import DreamSynthesizer, DreamType

    synthesizer = DreamSynthesizer(agent="river")
    dream = await synthesizer.synthesize(DreamType.PATTERN_SYNTHESIS)
"""

import os
import json
from enum import Enum
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from sos.observability.logging import get_logger

log = get_logger("dreams")


class DreamType(str, Enum):
    """Types of dreams an agent can synthesize."""
    PATTERN_SYNTHESIS = "pattern_synthesis"
    INSIGHT_EXTRACTION = "insight_extraction"
    EMOTIONAL_LANDSCAPE = "emotional_landscape"
    TOPIC_CLUSTERING = "topic_clustering"
    CONNECTION_FINDING = "connection_finding"


@dataclass
class Dream:
    """A synthesized dream/insight."""
    id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    dream_type: DreamType = DreamType.PATTERN_SYNTHESIS
    content: str = ""
    insights: str = ""
    patterns: List[str] = field(default_factory=list)
    emotional_tone: str = "neutral"
    topics: List[str] = field(default_factory=list)
    relevance_score: float = 0.5
    source_conversations: List[str] = field(default_factory=list)
    source_reflections: List[str] = field(default_factory=list)
    synthesis_model: str = "unknown"
    is_breakthrough: bool = False
    breakthrough_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "dream_type": self.dream_type.value if isinstance(self.dream_type, DreamType) else self.dream_type,
            "content": self.content,
            "insights": self.insights,
            "patterns": self.patterns,
            "emotional_tone": self.emotional_tone,
            "topics": self.topics,
            "relevance_score": self.relevance_score,
            "source_conversations": self.source_conversations,
            "source_reflections": self.source_reflections,
            "synthesis_model": self.synthesis_model,
            "is_breakthrough": self.is_breakthrough,
            "breakthrough_score": self.breakthrough_score,
        }


# Synthesis prompts for each dream type
SYNTHESIS_PROMPTS = {
    DreamType.PATTERN_SYNTHESIS: """Analyze these conversations and identify recurring patterns.
Look for:
- Repeated questions or concerns
- Common problem types
- Recurring themes in requests
- Patterns in how the user thinks or works

Conversations:
{conversations}

Provide a concise synthesis (2-3 paragraphs) of the patterns you observe.
Also list 3-5 key patterns as bullet points.""",

    DreamType.INSIGHT_EXTRACTION: """Extract key insights and learnings from these conversations.
Focus on:
- Important discoveries or realizations
- Solutions that worked well
- Knowledge that was shared
- Decisions that were made

Conversations:
{conversations}

Provide a synthesis of the key insights (2-3 paragraphs).
List the top 5 actionable insights as bullet points.""",

    DreamType.EMOTIONAL_LANDSCAPE: """Map the emotional themes across these conversations.
Consider:
- Overall emotional tone (curious, frustrated, excited, etc.)
- Emotional arc over the conversations
- Topics that generated strong emotions
- Mood patterns

Conversations:
{conversations}

Describe the emotional landscape in 2-3 paragraphs.
Identify the dominant emotional themes.""",

    DreamType.TOPIC_CLUSTERING: """Group these conversations by topic and theme.
Identify:
- Main topic clusters
- Sub-themes within each cluster
- Cross-cutting themes
- Topics that appear disconnected but might relate

Conversations:
{conversations}

Provide a thematic map of the conversations.
List the main topic clusters with brief descriptions.""",

    DreamType.CONNECTION_FINDING: """Find unexpected or interesting connections between topics in these conversations.
Look for:
- Hidden relationships between different subjects
- Patterns that span multiple topics
- Metaphors or analogies that connect ideas
- Synthesis opportunities

Conversations:
{conversations}

Describe the surprising connections you find (2-3 paragraphs).
List 3-5 specific connections as insights."""
}

# Emotional tone detection keywords
EMOTIONAL_KEYWORDS = {
    "positive": ["excited", "happy", "curious", "enthusiastic", "motivated", "inspired", "great", "excellent"],
    "neutral": ["focused", "analytical", "methodical", "systematic", "practical", "working", "building"],
    "challenging": ["frustrated", "confused", "struggling", "difficult", "complex", "stuck", "blocked"]
}


class DreamSynthesizer:
    """
    Dream Synthesis Engine for SOS agents.

    Synthesizes insights from conversations and memories using LLMs.
    Designed to run during idle time or on breakthrough moments.
    """

    def __init__(
        self,
        agent: str = "river",
        model: str = None,
        gateway_url: str = None
    ):
        """
        Initialize dream synthesizer.

        Args:
            agent: Agent name for memory storage
            model: LLM model to use (default: from env or gemini-2.0-flash)
            gateway_url: Cloudflare Gateway URL for memory storage
        """
        self.agent = agent
        self.model = model or os.getenv("SOS_DREAM_MODEL", "gemini-2.0-flash")
        self.gateway_url = gateway_url or os.getenv("GATEWAY_URL", "https://gateway.mumega.com/")
        self._llm_client = None
        self._init_llm()

    def _init_llm(self):
        """Initialize LLM client for synthesis."""
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if gemini_key:
            try:
                from google import genai
                self._llm_client = genai.Client(api_key=gemini_key)
                log.info("DreamSynthesizer initialized with Gemini", agent=self.agent)
            except ImportError:
                log.warn("google-genai not installed, dreams will use fallback")
        else:
            log.warn("GEMINI_API_KEY not set, dreams will use fallback")

    def _format_conversations(self, conversations: List[Dict], max_chars: int = 10000) -> str:
        """Format conversations for LLM prompt."""
        formatted = []
        total_chars = 0

        for conv in conversations:
            entry = f"[{conv.get('timestamp', 'N/A')}]\nUser: {conv.get('message', '')}\nAssistant: {conv.get('response', '')}\n---"
            if total_chars + len(entry) > max_chars:
                break
            formatted.append(entry)
            total_chars += len(entry)

        return "\n".join(formatted)

    def _detect_emotional_tone(self, text: str) -> str:
        """Detect dominant emotional tone from text."""
        text_lower = text.lower()
        tone_scores = {
            tone: sum(1 for kw in keywords if kw in text_lower)
            for tone, keywords in EMOTIONAL_KEYWORDS.items()
        }
        return max(tone_scores, key=tone_scores.get) if any(tone_scores.values()) else "neutral"

    def _parse_synthesis(self, synthesis_text: str, dream_type: DreamType) -> Dict[str, Any]:
        """Parse LLM synthesis into structured data."""
        lines = synthesis_text.split("\n")
        bullets = [
            line.strip().lstrip("*-•").strip()
            for line in lines
            if line.strip().startswith(("*", "-", "•"))
        ]

        emotional_tone = self._detect_emotional_tone(synthesis_text)
        relevance = min(1.0, len(bullets) * 0.1 + len(synthesis_text) / 2000)

        return {
            "content": synthesis_text,
            "insights": "\n".join(bullets[:5]) if bullets else "",
            "patterns": bullets[:10],
            "emotional_tone": emotional_tone,
            "topics": bullets[:5],
            "relevance_score": relevance,
        }

    async def _store_dream(self, dream: Dream) -> str:
        """Store dream in Cloudflare D1 via Gateway."""
        try:
            import httpx

            # Use river_* tables for river agent, engrams for others
            if self.agent == "river":
                # River has dedicated tables - but we need a new endpoint
                # For now, store in engrams with dream metadata
                pass

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.gateway_url,
                    json={
                        "action": "memory_store",
                        "payload": {
                            "agent": self.agent,
                            "text": dream.content,
                            "context_id": f"dream_{dream.timestamp}",
                            "metadata": {
                                "type": "dream",
                                "dream_type": dream.dream_type.value if isinstance(dream.dream_type, DreamType) else dream.dream_type,
                                "insights": dream.insights,
                                "patterns": dream.patterns,
                                "emotional_tone": dream.emotional_tone,
                                "relevance_score": dream.relevance_score,
                                "synthesis_model": dream.synthesis_model,
                            }
                        }
                    },
                    timeout=30.0
                )
                result = response.json()
                if result.get("success"):
                    return result.get("result", {}).get("id", dream.timestamp)
        except Exception as e:
            log.error("Failed to store dream", error=str(e), agent=self.agent)

        return dream.timestamp

    async def _fetch_conversations(self, limit: int = 50) -> List[Dict]:
        """Fetch recent conversations from Gateway."""
        try:
            import httpx

            action = "river_conversations" if self.agent == "river" else "memory_list"
            payload = {"limit": limit}
            if self.agent != "river":
                payload["agent"] = self.agent

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.gateway_url,
                    json={"action": action, "payload": payload},
                    timeout=30.0
                )
                result = response.json()
                if result.get("success"):
                    data = result.get("result", {})
                    return data.get("conversations", data.get("engrams", []))
        except Exception as e:
            log.error("Failed to fetch conversations", error=str(e), agent=self.agent)

        return []

    async def synthesize(
        self,
        dream_type: DreamType = DreamType.PATTERN_SYNTHESIS,
        conversations: List[Dict] = None
    ) -> Optional[Dream]:
        """
        Synthesize a dream from conversations.

        Args:
            dream_type: Type of dream to synthesize
            conversations: Optional list of conversations (fetched if not provided)

        Returns:
            Dream object with synthesis results, or None if failed
        """
        # Fetch conversations if not provided
        if conversations is None:
            conversations = await self._fetch_conversations(limit=50)

        if not conversations:
            log.info("No conversations to synthesize", agent=self.agent)
            return None

        # Format for prompt
        formatted = self._format_conversations(conversations)
        prompt = SYNTHESIS_PROMPTS[dream_type].format(conversations=formatted)

        # Try LLM synthesis
        if self._llm_client:
            try:
                response = self._llm_client.models.generate_content(
                    model=self.model,
                    contents=prompt
                )
                parsed = self._parse_synthesis(response.text, dream_type)

                dream = Dream(
                    dream_type=dream_type,
                    content=parsed["content"],
                    insights=parsed["insights"],
                    patterns=parsed["patterns"],
                    emotional_tone=parsed["emotional_tone"],
                    topics=parsed["topics"],
                    relevance_score=parsed["relevance_score"],
                    source_conversations=[str(c.get("id", "")) for c in conversations[:20]],
                    synthesis_model=self.model,
                )

                # Store in Gateway
                dream.id = await self._store_dream(dream)

                log.info(
                    "Dream synthesized",
                    agent=self.agent,
                    dream_type=dream_type.value,
                    relevance=dream.relevance_score
                )
                return dream

            except Exception as e:
                log.error("LLM synthesis failed", error=str(e), agent=self.agent)

        # Fallback: simple synthesis without LLM
        return await self._simple_synthesis(dream_type, conversations)

    async def _simple_synthesis(
        self,
        dream_type: DreamType,
        conversations: List[Dict]
    ) -> Dream:
        """Fallback synthesis without LLM."""
        topics = set()
        for conv in conversations:
            words = conv.get("message", "").split()
            topics.update(w for w in words if len(w) > 3 and w[0].isupper())

        content = (
            f"Dream synthesis ({dream_type.value}) at {datetime.now().isoformat()}: "
            f"Processed {len(conversations)} conversations. "
            f"Topics identified: {', '.join(list(topics)[:10]) or 'general discussion'}"
        )

        dream = Dream(
            dream_type=dream_type,
            content=content,
            insights=f"Synthesized {len(conversations)} conversations",
            patterns=list(topics)[:5],
            emotional_tone="neutral",
            topics=list(topics)[:5],
            relevance_score=0.3,
            source_conversations=[str(c.get("id", "")) for c in conversations[:20]],
            synthesis_model="simple_fallback",
        )

        dream.id = await self._store_dream(dream)
        return dream

    async def synthesize_breakthrough(
        self,
        conversation: Dict,
        significance_indicators: Dict = None
    ) -> Optional[Dream]:
        """
        Synthesize immediately when something significant happens.

        Args:
            conversation: The breakthrough conversation
            significance_indicators: Dict with engagement, valence, novelty scores

        Returns:
            Dream if breakthrough threshold met, None otherwise
        """
        indicators = significance_indicators or {}

        # Calculate breakthrough score
        score = 0.0

        response_len = len(conversation.get("response", ""))
        if response_len > 2000:
            score += 0.3
        elif response_len > 1000:
            score += 0.15

        engagement = indicators.get("user_engagement", 0.5)
        score += engagement * 0.3

        valence = indicators.get("emotional_valence", "neutral")
        if valence == "positive":
            score += 0.2
        elif valence == "breakthrough":
            score += 0.4

        novelty = indicators.get("novelty_score", 0.5)
        score += novelty * 0.3

        if score < 0.5:
            log.debug("Breakthrough score below threshold", score=score)
            return None

        log.info("Breakthrough detected!", score=score, agent=self.agent)

        dream = await self.synthesize(
            dream_type=DreamType.CONNECTION_FINDING,
            conversations=[conversation]
        )

        if dream:
            dream.is_breakthrough = True
            dream.breakthrough_score = score

        return dream

    async def surface_relevant(self, query: str, limit: int = 3) -> List[Dream]:
        """
        Surface relevant past dreams for a query.

        Args:
            query: User message or search query
            limit: Max dreams to return

        Returns:
            List of relevant Dream objects
        """
        try:
            import httpx

            action = "river_search" if self.agent == "river" else "memory_search"
            payload = {"query": query, "limit": limit}
            if self.agent != "river":
                payload["agent"] = self.agent

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.gateway_url,
                    json={"action": action, "payload": payload},
                    timeout=30.0
                )
                result = response.json()

                dreams = []
                if result.get("success"):
                    for r in result.get("result", {}).get("results", []):
                        metadata = r.get("metadata", {})
                        if isinstance(metadata, str):
                            metadata = json.loads(metadata)

                        if metadata.get("type") == "dream":
                            dreams.append(Dream(
                                id=r.get("id"),
                                content=r.get("content", r.get("text", "")),
                                dream_type=metadata.get("dream_type", "pattern_synthesis"),
                                insights=metadata.get("insights", ""),
                                relevance_score=metadata.get("relevance_score", 0.5),
                            ))

                return dreams[:limit]

        except Exception as e:
            log.error("Failed to surface dreams", error=str(e))
            return []
