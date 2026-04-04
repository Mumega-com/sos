"""
SOS adapters — unified LLM interface with budget tracking.

Public surface:
    AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo  — base types
    ClaudeAdapter, GeminiAdapter, OpenAIAdapter               — concrete adapters
    ModelRouter, AgentConfig                                   — routing layer
"""
from sos.adapters.base import (
    AgentAdapter,
    ExecutionContext,
    ExecutionResult,
    UsageInfo,
)
from sos.adapters.claude_adapter import ClaudeAdapter
from sos.adapters.gemini_adapter import GeminiAdapter
from sos.adapters.openai_adapter import OpenAIAdapter
from sos.adapters.router import AgentConfig, ModelRouter

__all__ = [
    "AgentAdapter",
    "ExecutionContext",
    "ExecutionResult",
    "UsageInfo",
    "ClaudeAdapter",
    "GeminiAdapter",
    "OpenAIAdapter",
    "AgentConfig",
    "ModelRouter",
]
