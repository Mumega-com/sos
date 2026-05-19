"""
SOS Memory Service Contract.

The Memory Service handles:
- Vector storage and retrieval
- Semantic search
- Memory lifecycle (store, retrieve, decay, consolidate)
- Mirror/swarm memory bridges
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from enum import Enum

from sos.kernel import Capability


class MemoryType(Enum):
    """Types of memories."""
    ENGRAM = "engram"  # Standard memory unit
    FACT = "fact"       # Verified factual information
    EPISODE = "episode" # Event/conversation memory
    SKILL = "skill"     # Learned capability
    CONTEXT = "context" # Contextual/session memory


@dataclass
class Memory:
    """
    A memory unit (engram) in SOS.

    Attributes:
        id: Unique memory identifier
        content: The actual memory content
        embedding: Vector embedding (if computed)
        memory_type: Type of memory
        agent_id: Agent this memory belongs to
        importance: Importance score (0.0 - 1.0)
        access_count: Number of times retrieved
        created_at: When memory was created
        accessed_at: Last access time
        metadata: Additional metadata
        relations: Related memory IDs
    """
    content: str
    agent_id: str
    id: Optional[str] = None
    embedding: Optional[list[float]] = None
    memory_type: MemoryType = MemoryType.ENGRAM
    importance: float = 0.5
    access_count: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    accessed_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    relations: list[str] = field(default_factory=list)


@dataclass
class MemoryQuery:
    """Query for memory search."""
    query: str
    agent_id: str
    limit: int = 10
    min_similarity: float = 0.7
    memory_types: Optional[list[MemoryType]] = None
    include_relations: bool = False
    capability: Optional[Capability] = None


@dataclass
class MemorySearchResult:
    """Result of memory search."""
    memory: Memory
    similarity: float
    highlights: list[str] = field(default_factory=list)


@dataclass
class StoreResult:
    """Result of storing a memory."""
    memory_id: str
    success: bool
    extracted_facts: list[str] = field(default_factory=list)
    related_memories: list[str] = field(default_factory=list)


class MemoryContract(ABC):
    """
    Abstract contract for the SOS Memory Service.

    All Memory implementations must conform to this interface.
    """

    @abstractmethod
    async def store(
        self,
        content: str,
        agent_id: str,
        memory_type: MemoryType = MemoryType.ENGRAM,
        importance: float = 0.5,
        metadata: Optional[dict] = None,
        capability: Optional[Capability] = None,
    ) -> StoreResult:
        """
        Store a new memory.

        Args:
            content: Memory content
            agent_id: Agent storing the memory
            memory_type: Type of memory
            importance: Importance score
            metadata: Additional metadata
            capability: Authorization capability

        Returns:
            StoreResult with memory ID and extracted info
        """
        pass

    @abstractmethod
    async def search(self, query: MemoryQuery) -> list[MemorySearchResult]:
        """
        Search memories semantically.

        Args:
            query: Search query parameters

        Returns:
            List of matching memories with similarity scores
        """
        pass

    @abstractmethod
    async def get(
        self,
        memory_id: str,
        capability: Optional[Capability] = None,
    ) -> Optional[Memory]:
        """
        Get a specific memory by ID.

        Args:
            memory_id: Memory identifier
            capability: Authorization capability

        Returns:
            Memory if found, None otherwise
        """
        pass

    @abstractmethod
    async def delete(
        self,
        memory_id: str,
        capability: Optional[Capability] = None,
    ) -> bool:
        """
        Delete a memory.

        Args:
            memory_id: Memory to delete
            capability: Authorization capability

        Returns:
            True if deleted, False if not found
        """
        pass

    @abstractmethod
    async def relate(
        self,
        memory_id: str,
        related_id: str,
        relation_type: str = "related",
        capability: Optional[Capability] = None,
    ) -> bool:
        """
        Create a relation between memories.

        Args:
            memory_id: Source memory
            related_id: Target memory
            relation_type: Type of relation
            capability: Authorization capability

        Returns:
            True if relation created
        """
        pass

    @abstractmethod
    async def consolidate(
        self,
        agent_id: str,
        capability: Optional[Capability] = None,
    ) -> int:
        """
        Consolidate/merge similar memories for an agent.

        Args:
            agent_id: Agent whose memories to consolidate
            capability: Authorization capability

        Returns:
            Number of memories consolidated
        """
        pass

    @abstractmethod
    async def decay(
        self,
        agent_id: str,
        threshold: float = 0.3,
        capability: Optional[Capability] = None,
    ) -> int:
        """
        Apply decay to low-importance, rarely-accessed memories.

        Args:
            agent_id: Agent whose memories to decay
            threshold: Decay threshold
            capability: Authorization capability

        Returns:
            Number of memories decayed/removed
        """
        pass

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """
        Get memory service health status.

        Returns:
            Health status with stats
        """
        pass

    @abstractmethod
    async def stats(self, agent_id: Optional[str] = None) -> dict[str, Any]:
        """
        Get memory statistics.

        Args:
            agent_id: Optional filter by agent

        Returns:
            Statistics dict
        """
        pass


# Embedding backend interface
class EmbeddingBackend(ABC):
    """Abstract interface for embedding backends."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Generate embedding for text."""
        pass

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding dimension."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend name."""
        pass
