"""
SovereignOS (SOS) - A sovereign, modular operating system for agents and humans.

Core principles:
- Microkernel: keep the core minimal and stable
- Strict boundaries: adapters never import heavy engine internals
- Capability-based access: tools and actions require explicit grants
- Offline-first, sovereign-by-default
"""

__version__ = "0.10.4"
__author__ = "Mumega Collective"

__all__ = [
    "Message",
    "Response",
    "Identity",
    "Capability",
    "Config",
    "__version__",
]


def __getattr__(name: str):
    """Lazy-load kernel exports.

    Importing ``sos.sdk`` should not require the full platform dependency set.
    Older callers can still use ``from sos import Message``; the heavy kernel
    import happens only when those symbols are actually requested.
    """
    if name in {"Message", "Response", "Identity", "Capability", "Config"}:
        from sos import kernel

        return getattr(kernel, name)
    raise AttributeError(f"module 'sos' has no attribute {name!r}")
