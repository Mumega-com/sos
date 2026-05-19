from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

@dataclass
class ChatRequest:
    message: str
    agent_id: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    tools_enabled: bool = False
    memory_enabled: bool = True
    witness_enabled: bool = False  # Added for Witness Protocol
    capability: Optional[Any] = None # Added for FMAAP support
    stream: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)  # For delegation tracking

@dataclass
class ChatResponse:
    content: str
    agent_id: str
    model_used: str
    conversation_id: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ToolCallRequest:
    tool_name: str
    arguments: Dict[str, Any]

@dataclass
class ToolCallResult:
    result: Any
    error: Optional[str] = None

class EngineContract:
    async def chat(self, request: ChatRequest) -> ChatResponse: ...
    async def execute_tool(self, request: ToolCallRequest) -> ToolCallResult: ...