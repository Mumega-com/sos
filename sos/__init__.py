"""
SovereignOS (SOS) - A sovereign, modular operating system for agents and humans.

Core principles:
- Microkernel: keep the core minimal and stable
- Strict boundaries: adapters never import heavy engine internals
- Capability-based access: tools and actions require explicit grants
- Offline-first, sovereign-by-default
"""

__version__ = "0.3.0"
__author__ = "Mumega Collective"

from sos.kernel import (
    Message,
    Response,
    Identity,
    Capability,
    Config,
)

__all__ = [
    "Message",
    "Response",
    "Identity",
    "Capability",
    "Config",
    "__version__",
]
