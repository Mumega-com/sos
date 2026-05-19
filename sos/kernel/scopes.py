"""
Scope-Based Authorization

Provides granular permission control for SOS services.

Usage:
    from sos.kernel.scopes import Scope, require_scope, check_scopes

    @require_scope(Scope.WRITE, Scope.MEMORY)
    async def store_memory(request, content: str):
        ...

    # Or check manually
    if not check_scopes(user_scopes, {Scope.ADMIN}):
        raise ScopeDeniedError(...)
"""

from enum import Enum
from functools import wraps
from typing import Set, Callable, Any, Optional
from dataclasses import dataclass, field


class Scope(str, Enum):
    """Permission scopes for SOS services."""

    # Agent scopes
    AGENT_READ = "agent.read"      # Read agent state, config
    AGENT_WRITE = "agent.write"    # Modify agent, send messages
    AGENT_ADMIN = "agent.admin"    # Create/delete agents

    # Memory scopes
    MEMORY_READ = "memory.read"    # Search, retrieve memories
    MEMORY_WRITE = "memory.write"  # Store, update memories
    MEMORY_DELETE = "memory.delete"  # Delete memories

    # Economy scopes
    ECONOMY_READ = "economy.read"      # View balance, history
    ECONOMY_TRANSACT = "economy.transact"  # Transfer, spend
    ECONOMY_ADMIN = "economy.admin"    # Mint, configure

    # Tools scopes
    TOOLS_LIST = "tools.list"      # List available tools
    TOOLS_EXECUTE = "tools.execute"  # Execute tools
    TOOLS_ADMIN = "tools.admin"    # Register/remove tools

    # Identity scopes
    IDENTITY_READ = "identity.read"    # Read identity info
    IDENTITY_PAIR = "identity.pair"    # Pairing operations
    IDENTITY_ADMIN = "identity.admin"  # Manage identities

    # System scopes
    SYSTEM_HEALTH = "system.health"    # Health checks
    SYSTEM_CONFIG = "system.config"    # View/modify config
    SYSTEM_ADMIN = "system.admin"      # Full system access


# Common scope sets for convenience
SCOPE_SETS = {
    "readonly": {
        Scope.AGENT_READ,
        Scope.MEMORY_READ,
        Scope.ECONOMY_READ,
        Scope.TOOLS_LIST,
        Scope.IDENTITY_READ,
        Scope.SYSTEM_HEALTH,
    },
    "user": {
        Scope.AGENT_READ,
        Scope.AGENT_WRITE,
        Scope.MEMORY_READ,
        Scope.MEMORY_WRITE,
        Scope.ECONOMY_READ,
        Scope.ECONOMY_TRANSACT,
        Scope.TOOLS_LIST,
        Scope.TOOLS_EXECUTE,
        Scope.IDENTITY_READ,
        Scope.SYSTEM_HEALTH,
    },
    "agent": {
        Scope.AGENT_READ,
        Scope.AGENT_WRITE,
        Scope.MEMORY_READ,
        Scope.MEMORY_WRITE,
        Scope.MEMORY_DELETE,
        Scope.ECONOMY_READ,
        Scope.ECONOMY_TRANSACT,
        Scope.TOOLS_LIST,
        Scope.TOOLS_EXECUTE,
        Scope.IDENTITY_READ,
        Scope.IDENTITY_PAIR,
        Scope.SYSTEM_HEALTH,
    },
    "admin": set(Scope),  # All scopes
}


# Method to required scopes mapping
METHOD_SCOPES = {
    # Chat/Agent methods
    "chat": {Scope.AGENT_READ, Scope.AGENT_WRITE},
    "agent_status": {Scope.AGENT_READ},
    "agent_create": {Scope.AGENT_ADMIN},
    "agent_delete": {Scope.AGENT_ADMIN},

    # Memory methods
    "memory_search": {Scope.MEMORY_READ},
    "memory_retrieve": {Scope.MEMORY_READ},
    "memory_store": {Scope.MEMORY_WRITE},
    "memory_update": {Scope.MEMORY_WRITE},
    "memory_delete": {Scope.MEMORY_DELETE},

    # Economy methods
    "economy_balance": {Scope.ECONOMY_READ},
    "economy_history": {Scope.ECONOMY_READ},
    "economy_transfer": {Scope.ECONOMY_TRANSACT},
    "economy_mint": {Scope.ECONOMY_ADMIN},

    # Tools methods
    "tools_list": {Scope.TOOLS_LIST},
    "tools_execute": {Scope.TOOLS_EXECUTE},
    "tools_register": {Scope.TOOLS_ADMIN},

    # Identity methods
    "identity_info": {Scope.IDENTITY_READ},
    "identity_pair": {Scope.IDENTITY_PAIR},
    "identity_create": {Scope.IDENTITY_ADMIN},

    # System methods
    "health": {Scope.SYSTEM_HEALTH},
    "config_get": {Scope.SYSTEM_CONFIG},
    "config_set": {Scope.SYSTEM_CONFIG},
}


@dataclass
class ScopeContext:
    """Context containing scope information for a request."""
    scopes: Set[Scope] = field(default_factory=set)
    subject: Optional[str] = None  # e.g., "agent:River", "user:123"
    issuer: Optional[str] = None   # Who issued the token


class ScopeDeniedError(Exception):
    """Raised when required scopes are not present."""

    def __init__(self, required: Set[Scope], provided: Set[Scope]):
        self.required = required
        self.provided = provided
        self.missing = required - provided
        super().__init__(
            f"Missing required scopes: {[s.value for s in self.missing]}"
        )


def check_scopes(provided: Set[Scope], required: Set[Scope]) -> bool:
    """
    Check if provided scopes satisfy required scopes.

    Args:
        provided: Set of scopes the caller has
        required: Set of scopes needed for the operation

    Returns:
        True if all required scopes are present
    """
    return required.issubset(provided)


def parse_scopes(scope_strings: list[str]) -> Set[Scope]:
    """
    Parse scope strings into Scope enum set.

    Args:
        scope_strings: List of scope strings like ["agent.read", "memory.write"]

    Returns:
        Set of Scope enum values (invalid scopes are skipped)
    """
    result = set()
    scope_map = {s.value: s for s in Scope}
    for s in scope_strings:
        if s in scope_map:
            result.add(scope_map[s])
    return result


def get_method_scopes(method: str) -> Set[Scope]:
    """
    Get required scopes for a method.

    Args:
        method: Method name like "chat", "memory_store"

    Returns:
        Set of required scopes (empty if method not found)
    """
    return METHOD_SCOPES.get(method, set())


def require_scope(*scopes: Scope) -> Callable:
    """
    Decorator to require specific scopes for a function.

    The decorated function must receive a request object with
    `state.scope_context` containing a ScopeContext.

    Usage:
        @require_scope(Scope.MEMORY_WRITE)
        async def store_memory(request, content: str):
            ...

    Raises:
        ScopeDeniedError: If required scopes are not present
    """
    required = set(scopes)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(request, *args, **kwargs):
            # Get scope context from request
            scope_ctx = getattr(getattr(request, 'state', None), 'scope_context', None)
            if scope_ctx is None:
                # No scope context - treat as no scopes
                provided = set()
            else:
                provided = scope_ctx.scopes

            if not check_scopes(provided, required):
                raise ScopeDeniedError(required, provided)

            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_method_scopes(method: str) -> Callable:
    """
    Decorator that uses METHOD_SCOPES mapping.

    Usage:
        @require_method_scopes("memory_store")
        async def store_memory(request, content: str):
            ...
    """
    required = get_method_scopes(method)
    if not required:
        # No scopes defined for this method - allow all
        def passthrough(func):
            return func
        return passthrough
    return require_scope(*required)


def expand_scope_set(name: str) -> Set[Scope]:
    """
    Expand a scope set name to its scopes.

    Args:
        name: One of "readonly", "user", "agent", "admin"

    Returns:
        Set of scopes for that role
    """
    return SCOPE_SETS.get(name, set())


# Scope checking utilities for manual use
def can_read_agent(scopes: Set[Scope]) -> bool:
    """Check if scopes allow reading agent info."""
    return Scope.AGENT_READ in scopes


def can_write_agent(scopes: Set[Scope]) -> bool:
    """Check if scopes allow writing to agent."""
    return Scope.AGENT_WRITE in scopes


def can_access_memory(scopes: Set[Scope]) -> bool:
    """Check if scopes allow memory access."""
    return Scope.MEMORY_READ in scopes or Scope.MEMORY_WRITE in scopes


def can_transact(scopes: Set[Scope]) -> bool:
    """Check if scopes allow economic transactions."""
    return Scope.ECONOMY_TRANSACT in scopes


def can_execute_tools(scopes: Set[Scope]) -> bool:
    """Check if scopes allow tool execution."""
    return Scope.TOOLS_EXECUTE in scopes


def is_admin(scopes: Set[Scope]) -> bool:
    """Check if scopes include system admin."""
    return Scope.SYSTEM_ADMIN in scopes
