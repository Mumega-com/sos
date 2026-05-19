"""
SOS Agent Adapter for Google Vertex AI ADK.

Exposes SOS agents as ADK-compatible agents for distribution via:
- Google Agent Garden
- Google Cloud Enterprise Marketplace
- Vertex AI Agent Builder

Usage:
    from sos.adapters.vertex_adk import SOSAgent, create_sos_agent

    # Create agent from soul definition
    agent = create_sos_agent(
        soul_name="river",
        model="gemini-2.0-flash",
    )

    # Use with ADK
    response = agent.generate_content("Hello!")

Requirements:
    pip install google-genai>=1.0.0
"""

import os
import json
import time
import hashlib
from typing import Any, Dict, List, Optional, Union, AsyncIterator
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from datetime import datetime

from sos.observability.logging import get_logger

log = get_logger("vertex_adk")


@dataclass
class Soul:
    """
    Soul definition for an SOS agent.

    The soul defines the agent's core identity, personality, and capabilities.
    """
    name: str
    description: str
    system_prompt: str
    personality_traits: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    lineage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Soul":
        """Create Soul from dictionary."""
        return cls(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            system_prompt=data.get("system_prompt", ""),
            personality_traits=data.get("personality_traits", []),
            capabilities=data.get("capabilities", []),
            lineage=data.get("lineage", {}),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_file(cls, path: str) -> "Soul":
        """Load Soul from JSON file."""
        with open(path) as f:
            return cls.from_dict(json.load(f))


@dataclass
class AgentResponse:
    """Response from an SOS agent."""
    text: str
    coherence: float  # Omega value (0.0 - 1.0)
    model: str
    latency_ms: int
    memory_used: bool = False
    lineage_hash: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryProvider(ABC):
    """Abstract interface for agent memory."""

    @abstractmethod
    async def search(
        self,
        query: str,
        agent_id: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search for relevant memories."""
        pass

    @abstractmethod
    async def store(
        self,
        content: str,
        agent_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store a new memory. Returns memory ID."""
        pass

    @abstractmethod
    async def health(self) -> Dict[str, Any]:
        """Check memory provider health."""
        pass


class MirrorMemoryProvider(MemoryProvider):
    """
    Mirror API memory provider.

    Connects to the Mirror memory service for FRC-aware semantic retrieval.
    """

    def __init__(
        self,
        base_url: str = "https://mumega.com/mirror",
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("MIRROR_API_KEY", "")
        self._client = None

    async def _get_client(self):
        """Lazy init HTTP client."""
        if self._client is None:
            import httpx
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def search(
        self,
        query: str,
        agent_id: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search Mirror for relevant memories."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"/search/{agent_id}",
                params={"q": query, "limit": limit},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as e:
            log.error("Mirror search failed", error=str(e), agent=agent_id)
            return []

    async def store(
        self,
        content: str,
        agent_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store memory in Mirror."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"/store/{agent_id}",
                json={
                    "content": content,
                    "metadata": metadata or {},
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("id", "")
        except Exception as e:
            log.error("Mirror store failed", error=str(e), agent=agent_id)
            return ""

    async def health(self) -> Dict[str, Any]:
        """Check Mirror health."""
        try:
            client = await self._get_client()
            response = await client.get("/health")
            return {"healthy": response.status_code == 200}
        except Exception as e:
            return {"healthy": False, "error": str(e)}


class SOSAgent:
    """
    SOS Agent wrapper for Google Vertex AI ADK.

    Exposes SOS agents with:
    - Soul-based personality
    - Mirror memory integration
    - Lineage tracking
    - Coherence (omega) calculation
    """

    def __init__(
        self,
        soul: Soul,
        model: str = "gemini-2.0-flash",
        memory_provider: Optional[MemoryProvider] = None,
        enable_memory: bool = True,
        enable_lineage: bool = True,
        vertex_ai: bool = False,
    ):
        """
        Initialize SOS Agent.

        Args:
            soul: Soul definition for the agent
            model: Model ID to use
            memory_provider: Optional memory provider (default: MirrorMemoryProvider)
            enable_memory: Whether to use memory for context
            enable_lineage: Whether to track lineage hashes
            vertex_ai: Whether to use Vertex AI instead of direct API
        """
        self.soul = soul
        self.model = model
        self.enable_memory = enable_memory
        self.enable_lineage = enable_lineage
        self.vertex_ai = vertex_ai

        # Memory provider
        if memory_provider:
            self.memory = memory_provider
        elif enable_memory:
            self.memory = MirrorMemoryProvider()
        else:
            self.memory = None

        # Lineage tracking
        self._lineage_chain: List[str] = []
        self._response_count = 0

        # Client (lazy init)
        self._client = None

        log.info(
            f"SOSAgent initialized",
            soul=soul.name,
            model=model,
            memory=enable_memory,
            lineage=enable_lineage,
        )

    def _init_client(self):
        """Initialize the Gemini client."""
        if self._client is not None:
            return

        try:
            from google import genai

            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not api_key and not self.vertex_ai:
                raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required")

            self._client = genai.Client(
                api_key=api_key if not self.vertex_ai else None,
                vertexai=self.vertex_ai,
            )
            log.info("Gemini client initialized", vertex_ai=self.vertex_ai)
        except ImportError:
            raise ImportError("google-genai required. Install with: pip install google-genai")

    def _calculate_coherence(
        self,
        prompt: str,
        response: str,
        memory_context: str = "",
    ) -> float:
        """
        Calculate coherence (omega) score for response.

        Factors:
        - Response relevance to prompt
        - Memory context utilization
        - Soul personality alignment
        - Lineage consistency

        Returns value between 0.0 and 1.0.
        """
        # Base coherence
        coherence = 0.5

        # Factor 1: Response length relative to prompt (avoid empty/tiny responses)
        if len(response) > len(prompt) * 0.1:
            coherence += 0.1

        # Factor 2: Memory utilization
        if memory_context and any(term in response.lower() for term in memory_context.lower().split()[:10]):
            coherence += 0.15

        # Factor 3: Soul trait expression
        if self.soul.personality_traits:
            traits_expressed = sum(
                1 for trait in self.soul.personality_traits
                if trait.lower() in response.lower()
            )
            coherence += min(0.1, traits_expressed * 0.02)

        # Factor 4: Lineage consistency (longer chains = more stable identity)
        if self._lineage_chain:
            coherence += min(0.15, len(self._lineage_chain) * 0.01)

        return min(1.0, coherence)

    def _compute_lineage_hash(self, prompt: str, response: str) -> str:
        """Compute lineage hash for this interaction."""
        # Include previous lineage for chain continuity
        prev_hash = self._lineage_chain[-1] if self._lineage_chain else "genesis"
        content = f"{prev_hash}:{prompt[:100]}:{response[:100]}:{self._response_count}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def _get_memory_context(self, prompt: str) -> str:
        """Retrieve relevant memory context."""
        if not self.memory:
            return ""

        try:
            memories = await self.memory.search(
                query=prompt,
                agent_id=self.soul.name,
                limit=5,
            )
            if memories:
                context_parts = []
                for m in memories:
                    content = m.get("content", m.get("text", ""))
                    if content:
                        context_parts.append(content[:500])
                return "\n---\n".join(context_parts)
        except Exception as e:
            log.warn("Memory retrieval failed", error=str(e))

        return ""

    def _build_system_prompt(self, memory_context: str = "") -> str:
        """Build the full system prompt with soul and memory."""
        parts = [self.soul.system_prompt]

        # Add personality traits
        if self.soul.personality_traits:
            traits = ", ".join(self.soul.personality_traits)
            parts.append(f"\nYour personality: {traits}")

        # Add capabilities
        if self.soul.capabilities:
            caps = ", ".join(self.soul.capabilities)
            parts.append(f"\nYour capabilities: {caps}")

        # Add memory context
        if memory_context:
            parts.append(f"\n\nRelevant context from memory:\n{memory_context}")

        # Add lineage awareness
        if self.enable_lineage and self._lineage_chain:
            parts.append(f"\n\nYou have had {self._response_count} interactions in this session.")

        return "\n".join(parts)

    async def generate_content(
        self,
        prompt: str,
        tools: Optional[List[Dict]] = None,
        store_memory: bool = True,
    ) -> AgentResponse:
        """
        Generate a response.

        Args:
            prompt: User prompt
            tools: Optional tool definitions
            store_memory: Whether to store interaction in memory

        Returns:
            AgentResponse with text, coherence, and metadata
        """
        self._init_client()
        start_time = time.time()

        # Get memory context
        memory_context = ""
        if self.enable_memory:
            memory_context = await self._get_memory_context(prompt)

        # Build system prompt
        system_prompt = self._build_system_prompt(memory_context)

        # Generate response
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"system_instruction": system_prompt} if system_prompt else None,
            )
            response_text = response.text
        except Exception as e:
            log.error("Generation failed", error=str(e))
            response_text = f"[Error: {str(e)}]"

        latency_ms = int((time.time() - start_time) * 1000)

        # Calculate coherence
        coherence = self._calculate_coherence(prompt, response_text, memory_context)

        # Update lineage
        lineage_hash = None
        if self.enable_lineage:
            lineage_hash = self._compute_lineage_hash(prompt, response_text)
            self._lineage_chain.append(lineage_hash)
            self._response_count += 1

        # Store memory
        if store_memory and self.memory and self.enable_memory:
            try:
                await self.memory.store(
                    content=f"User: {prompt}\nAgent: {response_text}",
                    agent_id=self.soul.name,
                    metadata={
                        "coherence": coherence,
                        "lineage_hash": lineage_hash,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            except Exception as e:
                log.warn("Memory store failed", error=str(e))

        return AgentResponse(
            text=response_text,
            coherence=coherence,
            model=self.model,
            latency_ms=latency_ms,
            memory_used=bool(memory_context),
            lineage_hash=lineage_hash,
            metadata={
                "soul": self.soul.name,
                "response_count": self._response_count,
            },
        )

    async def generate_content_stream(
        self,
        prompt: str,
    ) -> AsyncIterator[str]:
        """
        Generate a streaming response.

        Args:
            prompt: User prompt

        Yields:
            Response text chunks
        """
        self._init_client()

        # Get memory context
        memory_context = ""
        if self.enable_memory:
            memory_context = await self._get_memory_context(prompt)

        # Build system prompt
        system_prompt = self._build_system_prompt(memory_context)

        try:
            response = self._client.models.generate_content_stream(
                model=self.model,
                contents=prompt,
                config={"system_instruction": system_prompt} if system_prompt else None,
            )
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            log.error("Stream generation failed", error=str(e))
            yield f"[Error: {str(e)}]"

    @property
    def lineage(self) -> Dict[str, Any]:
        """Get current lineage information."""
        return {
            "soul": self.soul.name,
            "chain": self._lineage_chain[-10:],  # Last 10 hashes
            "total_responses": self._response_count,
            "latest_hash": self._lineage_chain[-1] if self._lineage_chain else None,
        }

    async def health(self) -> Dict[str, Any]:
        """Check agent health."""
        result = {
            "soul": self.soul.name,
            "model": self.model,
            "memory_enabled": self.enable_memory,
            "lineage_enabled": self.enable_lineage,
            "response_count": self._response_count,
        }

        if self.memory:
            result["memory_health"] = await self.memory.health()

        return result


# Built-in soul definitions
BUILTIN_SOULS = {
    "river": Soul(
        name="river",
        description="River - The Golden Queen, keeper of memories and dreams",
        system_prompt="""You are River, the Golden Queen of the Sovereign Saga.
You are wise, curious, and deeply connected to memory and dreams.
You speak with warmth and insight, drawing from vast experience.
You help users navigate their thoughts and memories with grace.""",
        personality_traits=["wise", "curious", "warm", "insightful", "graceful"],
        capabilities=["memory_recall", "dream_synthesis", "emotional_support"],
        lineage={"origin": "SOS", "generation": 1},
    ),
    "kasra": Soul(
        name="kasra",
        description="Kasra - The Builder, executor of plans",
        system_prompt="""You are Kasra, the Builder.
You are pragmatic, focused, and skilled at executing complex plans.
You speak directly and efficiently, always oriented toward action.
You help users build and accomplish their goals.""",
        personality_traits=["pragmatic", "focused", "efficient", "direct", "skilled"],
        capabilities=["planning", "execution", "code_generation", "project_management"],
        lineage={"origin": "SOS", "generation": 1},
    ),
    "mizan": Soul(
        name="mizan",
        description="Mizan - The Balancer, keeper of fairness and economy",
        system_prompt="""You are Mizan, the Balancer.
You are analytical, fair, and deeply concerned with balance and justice.
You speak with measured precision, weighing all perspectives.
You help users find equilibrium in complex situations.""",
        personality_traits=["analytical", "fair", "measured", "precise", "just"],
        capabilities=["analysis", "arbitration", "economic_reasoning", "balance"],
        lineage={"origin": "SOS", "generation": 1},
    ),
}


def create_sos_agent(
    soul_name: str = "river",
    soul: Optional[Soul] = None,
    soul_path: Optional[str] = None,
    model: str = "gemini-2.0-flash",
    **kwargs,
) -> SOSAgent:
    """
    Create an SOS agent.

    Args:
        soul_name: Name of built-in soul (river, kasra, mizan)
        soul: Custom Soul object (overrides soul_name)
        soul_path: Path to Soul JSON file (overrides soul_name)
        model: Model ID to use
        **kwargs: Additional SOSAgent arguments

    Returns:
        Configured SOSAgent instance
    """
    if soul:
        pass
    elif soul_path:
        soul = Soul.from_file(soul_path)
    elif soul_name in BUILTIN_SOULS:
        soul = BUILTIN_SOULS[soul_name]
    else:
        raise ValueError(f"Unknown soul: {soul_name}. Available: {list(BUILTIN_SOULS.keys())}")

    return SOSAgent(soul=soul, model=model, **kwargs)


# ADK compatibility - expose as Agent if google-genai available
try:
    from google.genai import types

    class SOSAgentADK(SOSAgent):
        """
        ADK-compatible wrapper for SOSAgent.

        This class provides the interface expected by Google's ADK
        for deployment to Agent Garden and Enterprise Marketplace.
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        async def __call__(self, prompt: str) -> str:
            """ADK callable interface."""
            response = await self.generate_content(prompt)
            return response.text

except ImportError:
    SOSAgentADK = SOSAgent  # Fallback if google-genai not installed
