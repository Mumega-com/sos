"""
SOS Agent Adapters — unified interface for any LLM provider.
Cherry-picked from Paperclip's adapter pattern, adapted for our Python stack.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


@dataclass
class ExecutionContext:
    """Context passed to adapter for each execution."""
    agent_id: str
    prompt: str
    system_prompt: str = ""
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: List[Dict] = field(default_factory=list)
    session_id: Optional[str] = None
    customer_id: Optional[str] = None
    project: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageInfo:
    """Token usage and cost info from a model call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cents: int = 0
    model: str = ""
    provider: str = ""


@dataclass
class ExecutionResult:
    """Result from adapter execution."""
    text: str
    usage: UsageInfo
    success: bool = True
    error: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentAdapter(ABC):
    """Base adapter interface. All LLM providers implement this."""

    provider: str = "unknown"

    @abstractmethod
    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        """Execute a prompt and return result with usage."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is reachable."""
        ...

    def estimate_cost(self, input_tokens: int, output_tokens: int, model: str) -> int:
        """Estimate cost in cents. Override per provider."""
        return 0
