"""
SOS adapters — unified LLM interface with budget tracking.

Public surface:
    AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo  — base types
    ClaudeAdapter, GeminiAdapter, OpenAIAdapter               — concrete adapters
    ModelRouter, AgentConfig                                   — routing layer

Concrete adapters are lazy-loaded via module ``__getattr__`` so importing the
base types / router does not require every LLM SDK (anthropic, google-genai,
openai) to be installed. Missing SDKs only surface when the matching adapter
class is actually referenced.
"""
from sos.adapters.base import (
    AgentAdapter,
    ExecutionContext,
    ExecutionResult,
    UsageInfo,
)
from sos.adapters.router import AgentConfig, ModelRouter

_LAZY = {
    "ClaudeAdapter": ("sos.adapters.claude_adapter", "ClaudeAdapter"),
    "GeminiAdapter": ("sos.adapters.gemini_adapter", "GeminiAdapter"),
    "OpenAIAdapter": ("sos.adapters.openai_adapter", "OpenAIAdapter"),
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
