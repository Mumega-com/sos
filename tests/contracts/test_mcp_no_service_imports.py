"""Contract tests enforcing the v0.4.7 R2-sweep guarantee for sos/mcp/sos_mcp_sse.py."""
from __future__ import annotations

import ast
from pathlib import Path


_ALLOWED_SERVICE_IMPORTS: frozenset[str] = frozenset({"sos.services.bus.discovery"})


def _mcp_path() -> Path:
    return Path(__file__).resolve().parents[2] / "sos" / "mcp" / "sos_mcp_sse.py"


def _collect_service_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Walk all AST nodes and return (lineno, module) for any sos.services.* import."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sos.services."):
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("sos.services."):
                found.append((node.lineno, mod))
    return found


def test_mcp_file_exists_and_parses() -> None:
    """Sanity check: the MCP file exists and is valid Python."""
    p = _mcp_path()
    assert p.exists(), f"MCP file not found at {p}"
    ast.parse(p.read_text())  # raises SyntaxError on broken code


def test_mcp_file_has_zero_service_imports() -> None:
    """No sos.services.* imports allowed except the bus.discovery infra shim."""
    p = _mcp_path()
    tree = ast.parse(p.read_text())
    leaks = [
        (lineno, mod)
        for lineno, mod in _collect_service_imports(tree)
        if mod not in _ALLOWED_SERVICE_IMPORTS
    ]
    assert leaks == [], (
        "R2-sweep violation — sos/mcp/sos_mcp_sse.py imports sos.services.* "
        "modules that must be moved to the kernel layer:\n"
        + "\n".join(f"  line {lineno}: {mod}" for lineno, mod in leaks)
    )


def test_allowed_exception_list_is_frozen() -> None:
    """The allowed-exception set must have exactly 1 entry; any addition requires an explicit test edit."""
    assert len(_ALLOWED_SERVICE_IMPORTS) == 1
    assert "sos.services.bus.discovery" in _ALLOWED_SERVICE_IMPORTS
