"""Code Mode MCP execution wrapper.

A token-efficient tool-calling pattern for SOS MCP: instead of round-tripping
full JSON params + results through the model context for every tool call, a
client submits a Python snippet like ``tools.add(2, 3)`` and the server runs
it against a pre-bound ``tools`` namespace exposing registered MCP tools. Only
the final expression value (plus captured stdout/stderr) is returned.

This mirrors Cloudflare's Code Mode MCP pattern. See
``docs/plans/2026-04-17-code-mode-mcp-adoption.md`` for the spec.

Not a security sandbox; enforces budget/shape, not isolation. Run only
snippets from authenticated MCP clients.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import ctypes
import io
import math
import threading
import time
import traceback
from types import SimpleNamespace
from typing import Any, Callable, TypedDict


_DEFAULT_ALLOWED_BUILTINS: frozenset[str] = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "float",
        "int",
        "len",
        "list",
        "max",
        "min",
        "range",
        "round",
        "str",
        "sum",
        "tuple",
        "zip",
        "print",
    }
)


class CodeModeResult(TypedDict):
    value: Any
    stdout: str
    stderr: str
    duration_ms: float
    token_estimate: int


def _estimate_tokens(value: Any, stdout: str) -> int:
    """Rough token estimate: ~4 chars per token, rounded up."""
    try:
        value_repr = repr(value)
    except Exception:  # noqa: BLE001 - defensive; a broken __repr__ shouldn't fail the caller
        value_repr = "<unreprable>"
    chars = len(value_repr) + len(stdout)
    return int(math.ceil(chars / 4))


def _build_restricted_globals(
    tools: dict[str, Callable[..., Any]],
    allowed_builtins: set[str] | None,
) -> dict[str, Any]:
    """Construct the restricted globals dict for ``exec``.

    Only allow-listed builtins are exposed. ``__import__`` is absent, so any
    ``import`` statement raises ``ImportError``. The ``tools`` dict is exposed
    as ``tools`` (a ``SimpleNamespace``) so snippets can write
    ``tools.<name>(...)`` naturally.
    """
    names = (
        set(allowed_builtins)
        if allowed_builtins is not None
        else set(_DEFAULT_ALLOWED_BUILTINS)
    )

    import builtins as _builtins

    safe_builtins: dict[str, Any] = {}
    for name in names:
        if hasattr(_builtins, name):
            safe_builtins[name] = getattr(_builtins, name)

    tools_ns = SimpleNamespace(**tools)

    return {
        "__builtins__": safe_builtins,
        "tools": tools_ns,
    }


def _split_last_expression(code: str) -> tuple[str, bool]:
    """Rewrite the snippet so the trailing expression (if any) is assigned to
    ``_last``. Returns ``(rewritten_source, had_trailing_expr)``.

    Raises ``SyntaxError`` on unparseable input; caller must handle.
    """
    tree = ast.parse(code, mode="exec")
    if not tree.body:
        return code, False

    last = tree.body[-1]
    if not isinstance(last, ast.Expr):
        return code, False

    assign = ast.Assign(
        targets=[ast.Name(id="_last", ctx=ast.Store())],
        value=last.value,
    )
    ast.copy_location(assign, last)
    ast.fix_missing_locations(assign)
    tree.body[-1] = assign
    return ast.unparse(tree), True


def _run_sync(
    code: str,
    tools: dict[str, Callable[..., Any]],
    allowed_builtins: set[str] | None,
) -> tuple[Any, str, str]:
    """Execute the snippet synchronously. Returns ``(value, stdout, stderr)``.

    Never raises; exceptions are formatted into ``stderr`` and value is ``None``.
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    value: Any = None

    try:
        rewritten, had_expr = _split_last_expression(code)
    except SyntaxError:
        stderr_buf.write(traceback.format_exc())
        return None, stdout_buf.getvalue(), stderr_buf.getvalue()

    restricted_globals = _build_restricted_globals(tools, allowed_builtins)
    local_ns: dict[str, Any] = {}

    try:
        with (
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            compiled = compile(rewritten, "<code-mode>", "exec")
            exec(compiled, restricted_globals, local_ns)  # noqa: S102 - intentional
        if had_expr:
            value = local_ns.get("_last")
    except BaseException:  # noqa: BLE001 - we deliberately swallow everything
        stderr_buf.write(traceback.format_exc())
        value = None

    return value, stdout_buf.getvalue(), stderr_buf.getvalue()


def _inject_exception(thread_id: int, exc_type: type[BaseException]) -> None:
    """Inject ``exc_type`` into the target thread via the CPython C API.

    This is the stdlib-only mechanism for interrupting a running Python thread.
    It wakes pure-Python CPU loops (like ``while True: pass``) between bytecode
    ops. It does NOT interrupt blocking C calls (e.g. ``time.sleep``), so
    timeouts remain best-effort.
    """
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id),
        ctypes.py_object(exc_type),
    )
    if res > 1:
        # Failure: clear the injection to avoid leaving a half-set exception.
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), None)


async def execute_snippet(
    code: str,
    tools: dict[str, Callable[..., Any]],
    timeout_s: float = 5.0,
    allowed_builtins: set[str] | None = None,
) -> CodeModeResult:
    """Execute a Python ``code`` snippet against a pre-bound ``tools`` namespace.

    The snippet runs in a restricted namespace with only ``allowed_builtins``
    available (plus the ``tools`` SimpleNamespace). stdout/stderr are captured.
    If the trailing statement is an expression, its value is returned as
    ``value``; otherwise ``value`` is ``None``.

    On timeout, ``stderr`` contains ``"Timeout after <s>s"`` and ``value`` is
    ``None``. On any other exception, ``stderr`` contains the traceback and
    ``value`` is ``None``. The function never re-raises.

    Timeout enforcement is best-effort: a CPython async-exception is injected
    into the worker thread, which wakes pure-Python CPU loops. Blocking C
    calls (e.g. ``time.sleep``, network I/O) will NOT be interrupted.
    """
    start = time.perf_counter()

    result_box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            value, stdout, stderr = _run_sync(code, tools, allowed_builtins)
            result_box["value"] = value
            result_box["stdout"] = stdout
            result_box["stderr"] = stderr
        except BaseException:  # noqa: BLE001 - capture injected TimeoutError etc.
            result_box["value"] = None
            result_box["stdout"] = ""
            result_box["stderr"] = traceback.format_exc()

    thread = threading.Thread(target=_worker, name="code-mode-worker", daemon=True)
    thread.start()

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, thread.join, timeout_s)

    if thread.is_alive():
        # Inject a TimeoutError into the worker thread; give it a brief grace
        # period to unwind, then give up and let it leak (daemon=True means
        # interpreter shutdown will reap it).
        tid = thread.ident
        if tid is not None:
            _inject_exception(tid, TimeoutError)
        await loop.run_in_executor(None, thread.join, 0.1)

        duration_ms = (time.perf_counter() - start) * 1000.0
        return CodeModeResult(
            value=None,
            stdout="",
            stderr=f"Timeout after {timeout_s}s",
            duration_ms=duration_ms,
            token_estimate=_estimate_tokens(None, ""),
        )

    value = result_box.get("value")
    stdout = result_box.get("stdout", "")
    stderr = result_box.get("stderr", "")

    duration_ms = (time.perf_counter() - start) * 1000.0
    return CodeModeResult(
        value=value,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        token_estimate=_estimate_tokens(value, stdout),
    )
