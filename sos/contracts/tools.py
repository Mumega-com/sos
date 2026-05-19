"""
SOS Tools Service Contract.

The Tools Service handles:
- Tool registry and discovery
- MCP server management
- Tool execution with sandboxing
- Capability gating for tool access

JSON-RPC 2.0 Specification:
- Request: {"jsonrpc": "2.0", "id": "...", "method": "...", "params": {...}}
- Response: {"jsonrpc": "2.0", "id": "...", "result": {...}} or {"jsonrpc": "2.0", "id": "...", "error": {...}}
- Error: {"code": int, "message": str, "data": optional}
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Optional, Dict, List, Union
from enum import Enum
import json
import uuid

from sos.kernel import Capability


JSONRPC_VERSION = "2.0"


class ToolCategory(Enum):
    """Categories of tools."""
    SEARCH = "search"       # Web search, document search
    CODE = "code"           # Code execution, analysis
    FILE = "file"           # File operations
    NETWORK = "network"     # Network requests
    DATA = "data"           # Data processing
    MEMORY = "memory"       # Memory operations (via memory service)
    SYSTEM = "system"       # System operations
    CUSTOM = "custom"       # Custom/plugin tools


class ToolStatus(Enum):
    """Tool availability status."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    DISABLED = "disabled"


class ToolsRpcErrorCode(Enum):
    """
    Standard JSON-RPC error codes for tools gateway.

    Ranges:
    - -32xxx: JSON-RPC spec errors
    - 401xx: Authentication/capability errors
    - 403xx: Authorization errors
    - 404xx: Not found errors
    - 429xx: Rate limiting
    - 500xx: Server errors
    """
    # JSON-RPC 2.0 spec errors
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Capability errors (401xx)
    CAPABILITY_REQUIRED = 40101
    CAPABILITY_INVALID = 40102
    CAPABILITY_EXPIRED = 40103

    # Authorization errors (403xx)
    CAPABILITY_DENIED = 40301

    # Not found errors (404xx)
    TOOL_NOT_FOUND = 40401
    SERVER_NOT_FOUND = 40402

    # Rate limiting (429xx)
    RATE_LIMITED = 42901

    # Server errors (500xx)
    TOOL_ERROR = 50001
    GATEWAY_ERROR = 50002
    TOOL_UNAVAILABLE = 50301
    TIMEOUT = 50401


class JsonRpcValidationError(Exception):
    """Raised when JSON-RPC validation fails."""

    def __init__(self, code: ToolsRpcErrorCode, message: str, data: Optional[Dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass
class ToolsRpcError:
    """
    JSON-RPC 2.0 error object for gateway responses.

    Attributes:
        code: Error code (ToolsRpcErrorCode or int)
        message: Human-readable error message
        data: Optional additional error data
    """
    code: Union[ToolsRpcErrorCode, int]
    message: str
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-RPC error format."""
        d = {
            "code": self.code.value if isinstance(self.code, ToolsRpcErrorCode) else self.code,
            "message": self.message,
        }
        if self.data is not None:
            d["data"] = self.data
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolsRpcError":
        """Create from dictionary."""
        code = data.get("code")
        # Try to convert to enum
        try:
            code = ToolsRpcErrorCode(code)
        except (ValueError, TypeError):
            pass
        return cls(
            code=code,
            message=data.get("message", "Unknown error"),
            data=data.get("data"),
        )

    @classmethod
    def parse_error(cls, details: str = "Invalid JSON") -> "ToolsRpcError":
        """Create parse error."""
        return cls(ToolsRpcErrorCode.PARSE_ERROR, f"Parse error: {details}")

    @classmethod
    def invalid_request(cls, details: str = "Invalid request") -> "ToolsRpcError":
        """Create invalid request error."""
        return cls(ToolsRpcErrorCode.INVALID_REQUEST, f"Invalid request: {details}")

    @classmethod
    def method_not_found(cls, method: str) -> "ToolsRpcError":
        """Create method not found error."""
        return cls(ToolsRpcErrorCode.METHOD_NOT_FOUND, f"Method not found: {method}")

    @classmethod
    def invalid_params(cls, details: str) -> "ToolsRpcError":
        """Create invalid params error."""
        return cls(ToolsRpcErrorCode.INVALID_PARAMS, f"Invalid params: {details}")

    @classmethod
    def internal_error(cls, details: str = "Internal error") -> "ToolsRpcError":
        """Create internal error."""
        return cls(ToolsRpcErrorCode.INTERNAL_ERROR, f"Internal error: {details}")


@dataclass
class ToolsRpcRequest:
    """
    JSON-RPC 2.0 request envelope for tools gateway.

    Attributes:
        jsonrpc: Must be "2.0"
        id: Request identifier (for correlation)
        method: Method name to call
        params: Method parameters
    """
    jsonrpc: str
    id: str
    method: str
    params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate request on creation."""
        if self.jsonrpc != JSONRPC_VERSION:
            raise JsonRpcValidationError(
                ToolsRpcErrorCode.INVALID_REQUEST,
                f"jsonrpc must be '{JSONRPC_VERSION}'",
            )
        if not self.id:
            raise JsonRpcValidationError(
                ToolsRpcErrorCode.INVALID_REQUEST,
                "id is required",
            )
        if not self.method:
            raise JsonRpcValidationError(
                ToolsRpcErrorCode.INVALID_REQUEST,
                "method is required",
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-RPC request format."""
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolsRpcRequest":
        """
        Create request from dictionary.

        Raises:
            JsonRpcValidationError: If validation fails
        """
        return cls(
            jsonrpc=data.get("jsonrpc", ""),
            id=data.get("id", ""),
            method=data.get("method", ""),
            params=data.get("params", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ToolsRpcRequest":
        """
        Create request from JSON string.

        Raises:
            JsonRpcValidationError: If parsing or validation fails
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise JsonRpcValidationError(
                ToolsRpcErrorCode.PARSE_ERROR,
                f"Invalid JSON: {e}",
            )
        return cls.from_dict(data)

    @classmethod
    def create(
        cls,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> "ToolsRpcRequest":
        """
        Create a new JSON-RPC request.

        Args:
            method: Method name to call
            params: Method parameters
            request_id: Request ID (generated if not provided)

        Returns:
            ToolsRpcRequest instance
        """
        return cls(
            jsonrpc=JSONRPC_VERSION,
            id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            method=method,
            params=params or {},
        )


@dataclass
class ToolsRpcResponse:
    """
    JSON-RPC 2.0 response envelope for tools gateway.

    Note: Either result or error must be present, but not both.

    Attributes:
        jsonrpc: Must be "2.0"
        id: Request identifier (from request)
        result: Success result (mutually exclusive with error)
        error: Error object (mutually exclusive with result)
    """
    jsonrpc: str
    id: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[ToolsRpcError] = None

    def __post_init__(self):
        """Validate response on creation."""
        if self.result is not None and self.error is not None:
            raise JsonRpcValidationError(
                ToolsRpcErrorCode.INVALID_REQUEST,
                "result and error are mutually exclusive",
            )

    @property
    def is_success(self) -> bool:
        """Check if this is a success response."""
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-RPC response format."""
        d = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        if self.error is not None:
            d["error"] = self.error.to_dict()
        else:
            d["result"] = self.result
        return d

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolsRpcResponse":
        """Create response from dictionary."""
        error_data = data.get("error")
        error = ToolsRpcError.from_dict(error_data) if error_data else None
        return cls(
            jsonrpc=data.get("jsonrpc", JSONRPC_VERSION),
            id=data.get("id", ""),
            result=data.get("result"),
            error=error,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ToolsRpcResponse":
        """Create response from JSON string."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise JsonRpcValidationError(
                ToolsRpcErrorCode.PARSE_ERROR,
                f"Invalid JSON: {e}",
            )
        return cls.from_dict(data)

    @classmethod
    def success(cls, request_id: str, result: Dict[str, Any]) -> "ToolsRpcResponse":
        """Create a success response."""
        return cls(
            jsonrpc=JSONRPC_VERSION,
            id=request_id,
            result=result,
        )

    @classmethod
    def failure(cls, request_id: str, error: ToolsRpcError) -> "ToolsRpcResponse":
        """Create an error response."""
        return cls(
            jsonrpc=JSONRPC_VERSION,
            id=request_id,
            error=error,
        )


class ToolsRpcDispatcher:
    """
    JSON-RPC method dispatcher for tools gateway.

    Handles method routing and response formatting.
    """

    def __init__(self):
        self._methods: Dict[str, Callable] = {}

    def register(self, method_name: str, handler: Callable) -> None:
        """Register a method handler."""
        self._methods[method_name] = handler

    def unregister(self, method_name: str) -> None:
        """Unregister a method handler."""
        self._methods.pop(method_name, None)

    def list_methods(self) -> List[str]:
        """List registered methods."""
        return list(self._methods.keys())

    async def dispatch(self, request: ToolsRpcRequest) -> ToolsRpcResponse:
        """
        Dispatch a JSON-RPC request to the appropriate handler.

        Args:
            request: The JSON-RPC request

        Returns:
            JSON-RPC response
        """
        handler = self._methods.get(request.method)
        if handler is None:
            return ToolsRpcResponse.failure(
                request.id,
                ToolsRpcError.method_not_found(request.method),
            )

        try:
            # Call handler (supports both sync and async)
            import asyncio

            if asyncio.iscoroutinefunction(handler):
                result = await handler(request.params)
            else:
                result = handler(request.params)

            return ToolsRpcResponse.success(request.id, result)

        except JsonRpcValidationError as e:
            return ToolsRpcResponse.failure(
                request.id,
                ToolsRpcError(e.code, e.message, e.data),
            )
        except Exception as e:
            return ToolsRpcResponse.failure(
                request.id,
                ToolsRpcError.internal_error(str(e)),
            )

    async def handle_json(self, json_str: str) -> str:
        """
        Handle a raw JSON-RPC request string.

        Args:
            json_str: JSON-RPC request as string

        Returns:
            JSON-RPC response as string
        """
        try:
            request = ToolsRpcRequest.from_json(json_str)
        except JsonRpcValidationError as e:
            return ToolsRpcResponse.failure(
                "",
                ToolsRpcError(e.code, e.message, e.data),
            ).to_json()

        response = await self.dispatch(request)
        return response.to_json()


@dataclass
class ToolDefinition:
    """
    Definition of a tool in SOS.

    Attributes:
        name: Unique tool name
        description: Human-readable description
        category: Tool category
        parameters: JSON Schema for parameters
        returns: Description of return value
        required_capability: Capability action required
        rate_limit: Rate limit (e.g., "10/minute")
        timeout_seconds: Execution timeout
        sandbox_required: Whether sandboxing is required
        metadata: Additional metadata
    """
    name: str
    description: str
    category: ToolCategory
    parameters: dict[str, Any]  # JSON Schema
    returns: str
    required_capability: str = "tool:execute"
    rate_limit: Optional[str] = None
    timeout_seconds: int = 30
    sandbox_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionRequest:
    """Request to execute a tool."""
    tool_name: str
    arguments: dict[str, Any]
    agent_id: str
    capability: Capability
    timeout_override: Optional[int] = None


@dataclass
class ToolExecutionResult:
    """Result of tool execution."""
    success: bool
    output: Any
    error: Optional[str] = None
    duration_ms: int = 0
    logs: list[str] = field(default_factory=list)


@dataclass
class MCPServer:
    """
    MCP (Model Context Protocol) server definition.

    Attributes:
        name: Server name
        url: Server URL or socket path
        transport: Transport type (http, stdio, sse)
        tools: List of tools provided
        status: Current status
        last_health_check: Last successful health check
    """
    name: str
    url: str
    transport: str = "http"  # http | stdio | sse
    tools: list[str] = field(default_factory=list)
    status: ToolStatus = ToolStatus.AVAILABLE
    last_health_check: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolsContract(ABC):
    """
    Abstract contract for the SOS Tools Service.

    All Tools implementations must conform to this interface.
    """

    @abstractmethod
    async def register_tool(
        self,
        definition: ToolDefinition,
        handler: Optional[Callable] = None,
        capability: Optional[Capability] = None,
    ) -> bool:
        """
        Register a new tool.

        Args:
            definition: Tool definition
            handler: Optional handler function (for local tools)
            capability: Authorization capability

        Returns:
            True if registered successfully
        """
        pass

    @abstractmethod
    async def unregister_tool(
        self,
        tool_name: str,
        capability: Optional[Capability] = None,
    ) -> bool:
        """
        Unregister a tool.

        Args:
            tool_name: Tool to unregister
            capability: Authorization capability

        Returns:
            True if unregistered
        """
        pass

    @abstractmethod
    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """
        Execute a tool.

        Args:
            request: Execution request

        Returns:
            Execution result
        """
        pass

    @abstractmethod
    async def get_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        """
        Get tool definition.

        Args:
            tool_name: Tool name

        Returns:
            Tool definition if found
        """
        pass

    @abstractmethod
    async def list_tools(
        self,
        category: Optional[ToolCategory] = None,
        available_only: bool = True,
    ) -> list[ToolDefinition]:
        """
        List available tools.

        Args:
            category: Filter by category
            available_only: Only show available tools

        Returns:
            List of tool definitions
        """
        pass

    @abstractmethod
    async def get_tool_status(self, tool_name: str) -> ToolStatus:
        """
        Get tool availability status.

        Args:
            tool_name: Tool to check

        Returns:
            Tool status
        """
        pass

    # MCP Server Management

    @abstractmethod
    async def register_mcp_server(
        self,
        server: MCPServer,
        capability: Optional[Capability] = None,
    ) -> bool:
        """
        Register an MCP server.

        Args:
            server: MCP server definition
            capability: Authorization capability

        Returns:
            True if registered
        """
        pass

    @abstractmethod
    async def unregister_mcp_server(
        self,
        server_name: str,
        capability: Optional[Capability] = None,
    ) -> bool:
        """
        Unregister an MCP server.

        Args:
            server_name: Server to unregister
            capability: Authorization capability

        Returns:
            True if unregistered
        """
        pass

    @abstractmethod
    async def list_mcp_servers(self) -> list[MCPServer]:
        """
        List registered MCP servers.

        Returns:
            List of MCP servers
        """
        pass

    @abstractmethod
    async def discover_mcp_tools(self, server_name: str) -> list[ToolDefinition]:
        """
        Discover tools from an MCP server.

        Args:
            server_name: Server to query

        Returns:
            List of discovered tools
        """
        pass

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """
        Get tools service health status.

        Returns:
            Health status with stats
        """
        pass


# Built-in tool definitions
BUILTIN_TOOLS = [
    ToolDefinition(
        name="web_search",
        description="Search the web for information",
        category=ToolCategory.SEARCH,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        returns="List of search results with title, url, and snippet",
        rate_limit="10/minute",
    ),
    ToolDefinition(
        name="read_file",
        description="Read contents of a file",
        category=ToolCategory.FILE,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "required": ["path"],
        },
        returns="File contents as string",
        required_capability="file:read",
    ),
    ToolDefinition(
        name="write_file",
        description="Write contents to a file",
        category=ToolCategory.FILE,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "required": ["path", "content"],
        },
        returns="Boolean indicating success",
        required_capability="file:write",
    ),
    ToolDefinition(
        name="execute_code",
        description="Execute code in a sandboxed environment",
        category=ToolCategory.CODE,
        parameters={
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "javascript"]},
                "code": {"type": "string", "description": "Code to execute"},
            },
            "required": ["language", "code"],
        },
        returns="Execution output and any errors",
        timeout_seconds=60,
        sandbox_required=True,
    ),
]
