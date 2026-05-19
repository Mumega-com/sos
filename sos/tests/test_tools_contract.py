"""
Tests for Tools Service JSON-RPC Contract.

Tests JSON-RPC 2.0 request/response handling, validation, and dispatch.
"""

import pytest
import json
from typing import Dict, Any

from sos.contracts.tools import (
    ToolsRpcRequest,
    ToolsRpcResponse,
    ToolsRpcError,
    ToolsRpcErrorCode,
    ToolsRpcDispatcher,
    JsonRpcValidationError,
    ToolCategory,
    ToolStatus,
    ToolDefinition,
    JSONRPC_VERSION,
)


class TestToolsRpcErrorCode:
    """Tests for error code enum."""

    def test_jsonrpc_spec_codes(self):
        """Test JSON-RPC 2.0 spec error codes."""
        assert ToolsRpcErrorCode.PARSE_ERROR.value == -32700
        assert ToolsRpcErrorCode.INVALID_REQUEST.value == -32600
        assert ToolsRpcErrorCode.METHOD_NOT_FOUND.value == -32601
        assert ToolsRpcErrorCode.INVALID_PARAMS.value == -32602
        assert ToolsRpcErrorCode.INTERNAL_ERROR.value == -32603

    def test_capability_codes(self):
        """Test capability error codes are in 401xx range."""
        assert 40100 < ToolsRpcErrorCode.CAPABILITY_REQUIRED.value < 40200
        assert 40100 < ToolsRpcErrorCode.CAPABILITY_INVALID.value < 40200
        assert 40100 < ToolsRpcErrorCode.CAPABILITY_EXPIRED.value < 40200

    def test_rate_limit_code(self):
        """Test rate limit code is in 429xx range."""
        assert 42900 < ToolsRpcErrorCode.RATE_LIMITED.value < 43000


class TestToolsRpcError:
    """Tests for ToolsRpcError."""

    def test_basic_error(self):
        """Test creating a basic error."""
        error = ToolsRpcError(
            code=ToolsRpcErrorCode.TOOL_NOT_FOUND,
            message="Tool 'foo' not found",
        )

        assert error.code == ToolsRpcErrorCode.TOOL_NOT_FOUND
        assert error.message == "Tool 'foo' not found"
        assert error.data is None

    def test_error_with_data(self):
        """Test error with additional data."""
        error = ToolsRpcError(
            code=ToolsRpcErrorCode.INVALID_PARAMS,
            message="Missing required parameter",
            data={"missing": ["query"]},
        )

        assert error.data == {"missing": ["query"]}

    def test_to_dict(self):
        """Test error serialization to dict."""
        error = ToolsRpcError(
            code=ToolsRpcErrorCode.RATE_LIMITED,
            message="Too many requests",
            data={"retry_after": 60},
        )

        d = error.to_dict()

        assert d["code"] == 42901
        assert d["message"] == "Too many requests"
        assert d["data"] == {"retry_after": 60}

    def test_to_dict_without_data(self):
        """Test error serialization excludes None data."""
        error = ToolsRpcError(
            code=ToolsRpcErrorCode.TOOL_ERROR,
            message="Execution failed",
        )

        d = error.to_dict()

        assert "data" not in d

    def test_from_dict(self):
        """Test error deserialization from dict."""
        d = {
            "code": -32600,
            "message": "Invalid request",
            "data": {"details": "missing id"},
        }

        error = ToolsRpcError.from_dict(d)

        assert error.code == ToolsRpcErrorCode.INVALID_REQUEST
        assert error.message == "Invalid request"
        assert error.data == {"details": "missing id"}

    def test_from_dict_unknown_code(self):
        """Test from_dict with unknown error code keeps as int."""
        d = {
            "code": 99999,
            "message": "Custom error",
        }

        error = ToolsRpcError.from_dict(d)

        assert error.code == 99999
        assert error.message == "Custom error"

    def test_factory_parse_error(self):
        """Test parse error factory."""
        error = ToolsRpcError.parse_error("Unexpected token")

        assert error.code == ToolsRpcErrorCode.PARSE_ERROR
        assert "Unexpected token" in error.message

    def test_factory_invalid_request(self):
        """Test invalid request factory."""
        error = ToolsRpcError.invalid_request("Missing jsonrpc")

        assert error.code == ToolsRpcErrorCode.INVALID_REQUEST
        assert "Missing jsonrpc" in error.message

    def test_factory_method_not_found(self):
        """Test method not found factory."""
        error = ToolsRpcError.method_not_found("unknown_method")

        assert error.code == ToolsRpcErrorCode.METHOD_NOT_FOUND
        assert "unknown_method" in error.message

    def test_factory_invalid_params(self):
        """Test invalid params factory."""
        error = ToolsRpcError.invalid_params("query must be string")

        assert error.code == ToolsRpcErrorCode.INVALID_PARAMS
        assert "query must be string" in error.message

    def test_factory_internal_error(self):
        """Test internal error factory."""
        error = ToolsRpcError.internal_error("Database connection failed")

        assert error.code == ToolsRpcErrorCode.INTERNAL_ERROR
        assert "Database connection failed" in error.message


class TestToolsRpcRequest:
    """Tests for ToolsRpcRequest."""

    def test_basic_request(self):
        """Test creating a basic request."""
        request = ToolsRpcRequest(
            jsonrpc="2.0",
            id="req_123",
            method="tool.execute",
            params={"tool_name": "web_search"},
        )

        assert request.jsonrpc == "2.0"
        assert request.id == "req_123"
        assert request.method == "tool.execute"
        assert request.params == {"tool_name": "web_search"}

    def test_validation_wrong_jsonrpc_version(self):
        """Test validation rejects wrong jsonrpc version."""
        with pytest.raises(JsonRpcValidationError) as exc_info:
            ToolsRpcRequest(
                jsonrpc="1.0",
                id="req_123",
                method="test",
            )

        assert exc_info.value.code == ToolsRpcErrorCode.INVALID_REQUEST
        assert "2.0" in exc_info.value.message

    def test_validation_missing_id(self):
        """Test validation rejects missing id."""
        with pytest.raises(JsonRpcValidationError) as exc_info:
            ToolsRpcRequest(
                jsonrpc="2.0",
                id="",
                method="test",
            )

        assert exc_info.value.code == ToolsRpcErrorCode.INVALID_REQUEST
        assert "id" in exc_info.value.message

    def test_validation_missing_method(self):
        """Test validation rejects missing method."""
        with pytest.raises(JsonRpcValidationError) as exc_info:
            ToolsRpcRequest(
                jsonrpc="2.0",
                id="req_123",
                method="",
            )

        assert exc_info.value.code == ToolsRpcErrorCode.INVALID_REQUEST
        assert "method" in exc_info.value.message

    def test_to_dict(self):
        """Test request serialization to dict."""
        request = ToolsRpcRequest(
            jsonrpc="2.0",
            id="req_456",
            method="tool.list",
            params={"category": "search"},
        )

        d = request.to_dict()

        assert d == {
            "jsonrpc": "2.0",
            "id": "req_456",
            "method": "tool.list",
            "params": {"category": "search"},
        }

    def test_to_json(self):
        """Test request serialization to JSON."""
        request = ToolsRpcRequest(
            jsonrpc="2.0",
            id="req_789",
            method="test",
        )

        json_str = request.to_json()
        parsed = json.loads(json_str)

        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == "req_789"

    def test_from_dict(self):
        """Test request deserialization from dict."""
        d = {
            "jsonrpc": "2.0",
            "id": "req_abc",
            "method": "tool.execute",
            "params": {"query": "test"},
        }

        request = ToolsRpcRequest.from_dict(d)

        assert request.id == "req_abc"
        assert request.method == "tool.execute"
        assert request.params == {"query": "test"}

    def test_from_json(self):
        """Test request deserialization from JSON."""
        json_str = '{"jsonrpc": "2.0", "id": "req_xyz", "method": "test", "params": {}}'

        request = ToolsRpcRequest.from_json(json_str)

        assert request.id == "req_xyz"
        assert request.method == "test"

    def test_from_json_invalid(self):
        """Test from_json with invalid JSON."""
        with pytest.raises(JsonRpcValidationError) as exc_info:
            ToolsRpcRequest.from_json("not valid json {")

        assert exc_info.value.code == ToolsRpcErrorCode.PARSE_ERROR

    def test_create_factory(self):
        """Test create factory method."""
        request = ToolsRpcRequest.create(
            method="tool.execute",
            params={"tool_name": "web_search"},
        )

        assert request.jsonrpc == "2.0"
        assert request.id.startswith("req_")
        assert request.method == "tool.execute"
        assert request.params == {"tool_name": "web_search"}

    def test_create_factory_with_custom_id(self):
        """Test create factory with custom ID."""
        request = ToolsRpcRequest.create(
            method="test",
            request_id="custom_id_123",
        )

        assert request.id == "custom_id_123"

    def test_default_empty_params(self):
        """Test default empty params."""
        request = ToolsRpcRequest(
            jsonrpc="2.0",
            id="req_test",
            method="test",
        )

        assert request.params == {}


class TestToolsRpcResponse:
    """Tests for ToolsRpcResponse."""

    def test_success_response(self):
        """Test creating a success response."""
        response = ToolsRpcResponse(
            jsonrpc="2.0",
            id="req_123",
            result={"status": "ok"},
        )

        assert response.jsonrpc == "2.0"
        assert response.id == "req_123"
        assert response.result == {"status": "ok"}
        assert response.error is None
        assert response.is_success is True

    def test_error_response(self):
        """Test creating an error response."""
        error = ToolsRpcError(ToolsRpcErrorCode.TOOL_NOT_FOUND, "Not found")
        response = ToolsRpcResponse(
            jsonrpc="2.0",
            id="req_456",
            error=error,
        )

        assert response.result is None
        assert response.error is not None
        assert response.is_success is False

    def test_validation_both_result_and_error(self):
        """Test validation rejects both result and error."""
        error = ToolsRpcError(ToolsRpcErrorCode.INTERNAL_ERROR, "Error")

        with pytest.raises(JsonRpcValidationError):
            ToolsRpcResponse(
                jsonrpc="2.0",
                id="req_789",
                result={"data": "something"},
                error=error,
            )

    def test_to_dict_success(self):
        """Test success response serialization."""
        response = ToolsRpcResponse(
            jsonrpc="2.0",
            id="req_abc",
            result={"tools": ["web_search", "file_read"]},
        )

        d = response.to_dict()

        assert d == {
            "jsonrpc": "2.0",
            "id": "req_abc",
            "result": {"tools": ["web_search", "file_read"]},
        }
        assert "error" not in d

    def test_to_dict_error(self):
        """Test error response serialization."""
        error = ToolsRpcError(ToolsRpcErrorCode.RATE_LIMITED, "Slow down")
        response = ToolsRpcResponse(
            jsonrpc="2.0",
            id="req_def",
            error=error,
        )

        d = response.to_dict()

        assert "error" in d
        assert d["error"]["code"] == 42901
        assert "result" not in d

    def test_to_json(self):
        """Test response serialization to JSON."""
        response = ToolsRpcResponse.success("req_test", {"ok": True})

        json_str = response.to_json()
        parsed = json.loads(json_str)

        assert parsed["result"]["ok"] is True

    def test_from_dict(self):
        """Test response deserialization from dict."""
        d = {
            "jsonrpc": "2.0",
            "id": "req_ghi",
            "result": {"count": 5},
        }

        response = ToolsRpcResponse.from_dict(d)

        assert response.id == "req_ghi"
        assert response.result == {"count": 5}
        assert response.is_success is True

    def test_from_dict_error(self):
        """Test error response deserialization."""
        d = {
            "jsonrpc": "2.0",
            "id": "req_jkl",
            "error": {
                "code": -32601,
                "message": "Method not found",
            },
        }

        response = ToolsRpcResponse.from_dict(d)

        assert response.is_success is False
        assert response.error.code == ToolsRpcErrorCode.METHOD_NOT_FOUND

    def test_from_json(self):
        """Test response deserialization from JSON."""
        json_str = '{"jsonrpc": "2.0", "id": "req_mno", "result": {}}'

        response = ToolsRpcResponse.from_json(json_str)

        assert response.id == "req_mno"
        assert response.is_success is True

    def test_from_json_invalid(self):
        """Test from_json with invalid JSON."""
        with pytest.raises(JsonRpcValidationError):
            ToolsRpcResponse.from_json("not valid")

    def test_success_factory(self):
        """Test success factory method."""
        response = ToolsRpcResponse.success(
            "req_pqr",
            {"executed": True, "output": "Hello"},
        )

        assert response.jsonrpc == "2.0"
        assert response.id == "req_pqr"
        assert response.result == {"executed": True, "output": "Hello"}
        assert response.is_success is True

    def test_failure_factory(self):
        """Test failure factory method."""
        error = ToolsRpcError(ToolsRpcErrorCode.TIMEOUT, "Timed out")
        response = ToolsRpcResponse.failure("req_stu", error)

        assert response.jsonrpc == "2.0"
        assert response.id == "req_stu"
        assert response.error == error
        assert response.is_success is False


class TestToolsRpcDispatcher:
    """Tests for ToolsRpcDispatcher."""

    @pytest.fixture
    def dispatcher(self):
        """Create a dispatcher instance."""
        return ToolsRpcDispatcher()

    def test_register_method(self, dispatcher):
        """Test registering a method handler."""
        def handler(params):
            return {"echo": params}

        dispatcher.register("test.echo", handler)

        assert "test.echo" in dispatcher.list_methods()

    def test_unregister_method(self, dispatcher):
        """Test unregistering a method handler."""
        dispatcher.register("test.remove", lambda p: {})
        dispatcher.unregister("test.remove")

        assert "test.remove" not in dispatcher.list_methods()

    def test_list_methods(self, dispatcher):
        """Test listing registered methods."""
        dispatcher.register("method.a", lambda p: {})
        dispatcher.register("method.b", lambda p: {})

        methods = dispatcher.list_methods()

        assert "method.a" in methods
        assert "method.b" in methods

    @pytest.mark.asyncio
    async def test_dispatch_sync_handler(self, dispatcher):
        """Test dispatching to a sync handler."""
        def echo_handler(params):
            return {"received": params.get("message")}

        dispatcher.register("echo", echo_handler)

        request = ToolsRpcRequest.create("echo", {"message": "hello"})
        response = await dispatcher.dispatch(request)

        assert response.is_success
        assert response.result == {"received": "hello"}

    @pytest.mark.asyncio
    async def test_dispatch_async_handler(self, dispatcher):
        """Test dispatching to an async handler."""
        async def async_handler(params):
            return {"async": True, "value": params.get("value", 0) * 2}

        dispatcher.register("double", async_handler)

        request = ToolsRpcRequest.create("double", {"value": 21})
        response = await dispatcher.dispatch(request)

        assert response.is_success
        assert response.result == {"async": True, "value": 42}

    @pytest.mark.asyncio
    async def test_dispatch_method_not_found(self, dispatcher):
        """Test dispatching unknown method."""
        request = ToolsRpcRequest.create("unknown.method", {})
        response = await dispatcher.dispatch(request)

        assert response.is_success is False
        assert response.error.code == ToolsRpcErrorCode.METHOD_NOT_FOUND
        assert "unknown.method" in response.error.message

    @pytest.mark.asyncio
    async def test_dispatch_handler_exception(self, dispatcher):
        """Test dispatching when handler raises exception."""
        def bad_handler(params):
            raise ValueError("Something went wrong")

        dispatcher.register("bad", bad_handler)

        request = ToolsRpcRequest.create("bad", {})
        response = await dispatcher.dispatch(request)

        assert response.is_success is False
        assert response.error.code == ToolsRpcErrorCode.INTERNAL_ERROR
        assert "Something went wrong" in response.error.message

    @pytest.mark.asyncio
    async def test_dispatch_handler_validation_error(self, dispatcher):
        """Test dispatching when handler raises JsonRpcValidationError."""
        def validating_handler(params):
            if "required" not in params:
                raise JsonRpcValidationError(
                    ToolsRpcErrorCode.INVALID_PARAMS,
                    "Missing required parameter",
                )
            return {"ok": True}

        dispatcher.register("validate", validating_handler)

        request = ToolsRpcRequest.create("validate", {})
        response = await dispatcher.dispatch(request)

        assert response.is_success is False
        assert response.error.code == ToolsRpcErrorCode.INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_handle_json_success(self, dispatcher):
        """Test handling raw JSON request."""
        dispatcher.register("ping", lambda p: {"pong": True})

        json_request = '{"jsonrpc": "2.0", "id": "req_1", "method": "ping", "params": {}}'
        json_response = await dispatcher.handle_json(json_request)

        response = json.loads(json_response)
        assert response["result"] == {"pong": True}

    @pytest.mark.asyncio
    async def test_handle_json_parse_error(self, dispatcher):
        """Test handling invalid JSON."""
        json_response = await dispatcher.handle_json("not valid json {")

        response = json.loads(json_response)
        assert "error" in response
        assert response["error"]["code"] == ToolsRpcErrorCode.PARSE_ERROR.value

    @pytest.mark.asyncio
    async def test_handle_json_validation_error(self, dispatcher):
        """Test handling JSON-RPC validation error."""
        # Missing method
        json_request = '{"jsonrpc": "2.0", "id": "req_1", "params": {}}'
        json_response = await dispatcher.handle_json(json_request)

        response = json.loads(json_response)
        assert "error" in response
        assert response["error"]["code"] == ToolsRpcErrorCode.INVALID_REQUEST.value


class TestToolDefinition:
    """Tests for ToolDefinition dataclass."""

    def test_basic_definition(self):
        """Test creating a basic tool definition."""
        tool = ToolDefinition(
            name="web_search",
            description="Search the web",
            category=ToolCategory.SEARCH,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
            returns="Search results",
        )

        assert tool.name == "web_search"
        assert tool.category == ToolCategory.SEARCH
        assert tool.timeout_seconds == 30  # default
        assert tool.sandbox_required is True  # default

    def test_custom_capability(self):
        """Test tool with custom capability requirement."""
        tool = ToolDefinition(
            name="file_write",
            description="Write to file",
            category=ToolCategory.FILE,
            parameters={"type": "object"},
            returns="Boolean",
            required_capability="file:write",
        )

        assert tool.required_capability == "file:write"

    def test_rate_limit(self):
        """Test tool with rate limit."""
        tool = ToolDefinition(
            name="api_call",
            description="Call external API",
            category=ToolCategory.NETWORK,
            parameters={"type": "object"},
            returns="API response",
            rate_limit="10/minute",
        )

        assert tool.rate_limit == "10/minute"


class TestToolCategory:
    """Tests for ToolCategory enum."""

    def test_all_categories(self):
        """Test all tool categories exist."""
        assert ToolCategory.SEARCH.value == "search"
        assert ToolCategory.CODE.value == "code"
        assert ToolCategory.FILE.value == "file"
        assert ToolCategory.NETWORK.value == "network"
        assert ToolCategory.DATA.value == "data"
        assert ToolCategory.MEMORY.value == "memory"
        assert ToolCategory.SYSTEM.value == "system"
        assert ToolCategory.CUSTOM.value == "custom"


class TestToolStatus:
    """Tests for ToolStatus enum."""

    def test_all_statuses(self):
        """Test all tool statuses exist."""
        assert ToolStatus.AVAILABLE.value == "available"
        assert ToolStatus.UNAVAILABLE.value == "unavailable"
        assert ToolStatus.RATE_LIMITED.value == "rate_limited"
        assert ToolStatus.DISABLED.value == "disabled"
