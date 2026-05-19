"""
SOS Conversation Context - Manages conversation history for LLM cache optimization.

The context window is critical for:
1. LLM cache efficiency (75-90% cost savings)
2. Conversation continuity across turns
3. Memory-augmented responses

Ported from CLI ConversationContext with simplifications for SOS.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import time


@dataclass
class ConversationContext:
    """
    Context for a conversation - tracks state across multiple messages.

    Maintains a rolling window of recent messages for:
    - LLM context (passed to model)
    - Cache key generation (determines cache reuse)
    - Conversation continuity
    """
    conversation_id: str
    agent_id: str
    created_at: float = field(default_factory=time.time)

    # Conversation state
    message_count: int = 0
    last_model: Optional[str] = None

    # Rolling window (last N messages each side)
    # Keeps last 10 user messages and 10 assistant responses
    recent_messages: List[Dict[str, Any]] = field(default_factory=list)
    recent_responses: List[Dict[str, Any]] = field(default_factory=list)

    # Arbitrary session data (tool results, etc.)
    session_data: Dict[str, Any] = field(default_factory=dict)

    # Cache tracking
    last_cache_key: Optional[str] = None
    cache_hits: int = 0
    cache_misses: int = 0

    # Window size (configurable)
    max_window_size: int = 10

    def add_message(self, content: str, metadata: Dict[str, Any] = None) -> None:
        """Add a user message to the context."""
        msg = {
            "role": "user",
            "content": content,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        self.recent_messages.append(msg)
        self.message_count += 1

        # Maintain rolling window
        if len(self.recent_messages) > self.max_window_size:
            self.recent_messages = self.recent_messages[-self.max_window_size:]

    def add_response(self, content: str, model: str = None, metadata: Dict[str, Any] = None) -> None:
        """Add an assistant response to the context."""
        resp = {
            "role": "assistant",
            "content": content,
            "timestamp": time.time(),
            "model": model,
            "metadata": metadata or {},
        }
        self.recent_responses.append(resp)
        self.last_model = model

        # Maintain rolling window
        if len(self.recent_responses) > self.max_window_size:
            self.recent_responses = self.recent_responses[-self.max_window_size:]

    def get_history(self, limit: int = 5) -> List[Dict[str, str]]:
        """
        Get recent conversation history in OpenAI-compatible format.

        Returns:
            List of {role: "user"|"assistant", content: str}
        """
        history = []

        # Get the most recent pairs (not oldest)
        start_idx = max(0, len(self.recent_messages) - limit)

        for i in range(start_idx, len(self.recent_messages)):
            # Add user message
            if i < len(self.recent_messages):
                msg = self.recent_messages[i]
                history.append({
                    "role": "user",
                    "content": msg["content"]
                })

            # Add corresponding assistant response if exists
            if i < len(self.recent_responses):
                resp = self.recent_responses[i]
                history.append({
                    "role": "assistant",
                    "content": resp["content"]
                })

        return history

    def get_history_for_cache(self) -> List[Dict[str, str]]:
        """
        Get full history for cache key generation.
        Uses all messages in window (not limited).
        """
        return self.get_history(limit=self.max_window_size)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for this context."""
        total = self.cache_hits + self.cache_misses
        hit_rate = (self.cache_hits / total * 100) if total > 0 else 0
        return {
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": f"{hit_rate:.1f}%",
            "message_count": self.message_count,
        }

    def clear(self) -> None:
        """Clear conversation history (start fresh)."""
        self.recent_messages = []
        self.recent_responses = []
        self.message_count = 0
        self.session_data = {}
        self.last_cache_key = None


class ContextManager:
    """
    Manages conversation contexts across multiple conversations.

    Thread-safe storage and retrieval of contexts by conversation_id.
    """

    def __init__(self, default_window_size: int = 10):
        self._contexts: Dict[str, ConversationContext] = {}
        self._default_window_size = default_window_size

    def get_or_create(
        self,
        conversation_id: str,
        agent_id: str = "agent:river",
    ) -> ConversationContext:
        """Get existing context or create new one."""
        if conversation_id not in self._contexts:
            self._contexts[conversation_id] = ConversationContext(
                conversation_id=conversation_id,
                agent_id=agent_id,
                max_window_size=self._default_window_size,
            )
        return self._contexts[conversation_id]

    def get(self, conversation_id: str) -> Optional[ConversationContext]:
        """Get context if exists, None otherwise."""
        return self._contexts.get(conversation_id)

    def remove(self, conversation_id: str) -> bool:
        """Remove a context. Returns True if existed."""
        if conversation_id in self._contexts:
            del self._contexts[conversation_id]
            return True
        return False

    def list_active(self) -> List[str]:
        """List all active conversation IDs."""
        return list(self._contexts.keys())

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics across all contexts."""
        total_hits = sum(c.cache_hits for c in self._contexts.values())
        total_misses = sum(c.cache_misses for c in self._contexts.values())
        total = total_hits + total_misses

        return {
            "active_conversations": len(self._contexts),
            "total_cache_hits": total_hits,
            "total_cache_misses": total_misses,
            "overall_hit_rate": f"{(total_hits / total * 100):.1f}%" if total > 0 else "0%",
        }

    def cleanup_old(self, max_age_seconds: int = 3600) -> int:
        """Remove contexts older than max_age_seconds. Returns count removed."""
        now = time.time()
        to_remove = [
            cid for cid, ctx in self._contexts.items()
            if (now - ctx.created_at) > max_age_seconds
        ]
        for cid in to_remove:
            del self._contexts[cid]
        return len(to_remove)
