"""
SOS CLI Frontends - Pluggable chat interfaces.

Frontends are swappable presentation layers for the SOS chat experience.
All frontends implement the same ChatFrontend interface.

Available frontends:
- repl: Simple Read-Eval-Print Loop (default)
- tui: Rich Terminal UI (future)
- web: Browser-based (future)

Usage:
    from sos.cli.frontends import get_frontend

    frontend = get_frontend("repl")
    await frontend.run(agent="river")
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from dataclasses import dataclass


@dataclass
class ChatConfig:
    """Configuration for chat frontends."""
    agent: str = "river"
    engine_url: str = "http://localhost:6060"
    streaming: bool = True
    conversation_id: Optional[str] = None
    show_metadata: bool = False


class ChatFrontend(ABC):
    """
    Abstract base class for chat frontends.

    Implement this interface to create new frontends (TUI, web, etc.)
    """

    def __init__(self, config: ChatConfig):
        self.config = config
        self._running = False

    @abstractmethod
    async def run(self) -> None:
        """Start the frontend main loop."""
        pass

    @abstractmethod
    async def get_input(self) -> str:
        """Get user input."""
        pass

    @abstractmethod
    async def display_chunk(self, chunk: str) -> None:
        """Display a streaming chunk."""
        pass

    @abstractmethod
    async def display_response(self, response: str, metadata: dict = None) -> None:
        """Display a complete response."""
        pass

    @abstractmethod
    async def display_error(self, error: str) -> None:
        """Display an error message."""
        pass

    @abstractmethod
    async def display_status(self, status: str) -> None:
        """Display a status message."""
        pass

    def stop(self) -> None:
        """Signal the frontend to stop."""
        self._running = False


# Frontend registry
_FRONTENDS = {}


def register_frontend(name: str):
    """Decorator to register a frontend."""
    def decorator(cls):
        _FRONTENDS[name] = cls
        return cls
    return decorator


def get_frontend(name: str, config: ChatConfig = None) -> ChatFrontend:
    """Get a frontend by name."""
    if name not in _FRONTENDS:
        available = ", ".join(_FRONTENDS.keys())
        raise ValueError(f"Unknown frontend: {name}. Available: {available}")

    config = config or ChatConfig()
    return _FRONTENDS[name](config)


def list_frontends() -> list[str]:
    """List available frontends."""
    return list(_FRONTENDS.keys())


# Import frontends to register them
from sos.cli.frontends.repl import REPLFrontend
