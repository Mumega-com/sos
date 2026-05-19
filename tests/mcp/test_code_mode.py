"""Tests for sos.mcp.code_mode.execute_snippet."""

from __future__ import annotations

import asyncio

import pytest

from sos.mcp.code_mode import execute_snippet


pytestmark = pytest.mark.asyncio


async def test_simple_expression_returns_value() -> None:
    result = await execute_snippet("1 + 2", tools={})
    assert result["value"] == 3
    assert result["stdout"] == ""
    assert result["stderr"] == ""


async def test_tool_call_via_namespace() -> None:
    tools = {"add": lambda a, b: a + b}
    result = await execute_snippet("tools.add(2, 3)", tools=tools)
    assert result["value"] == 5
    assert result["stderr"] == ""


async def test_statements_plus_final_expression() -> None:
    tools = {"add": lambda a, b: a + b}
    code = "x = tools.add(2, 3)\nx * 10"
    result = await execute_snippet(code, tools=tools)
    assert result["value"] == 50
    assert result["stderr"] == ""


async def test_print_captured_in_stdout() -> None:
    result = await execute_snippet("print('hi')", tools={})
    assert result["stdout"] == "hi\n"
    assert result["value"] is None
    assert result["stderr"] == ""


async def test_import_rejected() -> None:
    result = await execute_snippet("import os", tools={})
    assert result["value"] is None
    assert "ImportError" in result["stderr"] or "not defined" in result["stderr"]


async def test_timeout() -> None:
    result = await execute_snippet("while True:\n    pass", tools={}, timeout_s=0.5)
    assert result["value"] is None
    assert "Timeout" in result["stderr"]


async def test_traceback_on_exception() -> None:
    result = await execute_snippet("1/0", tools={})
    assert result["value"] is None
    assert "ZeroDivisionError" in result["stderr"]


async def test_token_estimate_small_for_int_result() -> None:
    result = await execute_snippet("1 + 2", tools={})
    assert result["token_estimate"] < 10


async def test_token_estimate_reflects_large_output() -> None:
    result = await execute_snippet("list(range(500))", tools={})
    assert result["token_estimate"] > 100


async def test_no_tools_namespace_still_works() -> None:
    result = await execute_snippet("42", tools={})
    assert result["value"] == 42
    assert result["stderr"] == ""


async def test_duration_ms_is_nonnegative() -> None:
    result = await execute_snippet("1 + 1", tools={})
    assert result["duration_ms"] >= 0


async def test_async_tool_callable_via_asyncio_run() -> None:
    """Async tools are NOT supported in this first pass.

    A snippet that calls an async function receives a coroutine object as its
    value; this test documents that limitation so we can revisit it when we
    need true async tool support.
    """

    async def async_add(a: int, b: int) -> int:
        return a + b

    tools = {"async_add": async_add}
    result = await execute_snippet("tools.async_add(2, 3)", tools=tools)

    # The returned value is a coroutine object (not the awaited result).
    assert asyncio.iscoroutine(result["value"])
    # Close the coroutine to suppress "never awaited" warnings.
    result["value"].close()
