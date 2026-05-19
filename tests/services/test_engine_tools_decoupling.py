"""v0.4.5 Wave 7 — engine → tools cleanup (P0-02).

The engine used to keep a redundant in-process ToolsCore alongside the
ToolsClient HTTP client. Wave 7 removed the in-proc import; engine now
routes tool execution exclusively through sos.clients.tools.ToolsClient.
"""
from __future__ import annotations

import ast
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[2] / "sos" / "services" / "engine"


def _imported_modules_in_tree(root: Path) -> set[str]:
    modules: set[str] = set()
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    modules.add(n.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
                for n in node.names:
                    modules.add(f"{node.module}.{n.name}")
    return modules


def test_engine_does_not_import_service_tools():
    mods = _imported_modules_in_tree(ENGINE_DIR)
    leaks = [m for m in mods if m.startswith("sos.services.tools")]
    assert leaks == [], f"engine still imports sos.services.tools: {leaks}"


def test_engine_uses_tools_client():
    """engine.core imports ToolsClient (HTTP) — the replacement path."""
    core_src = (ENGINE_DIR / "core.py").read_text(encoding="utf-8")
    assert "from sos.clients.tools import ToolsClient" in core_src
    assert "ToolsCore" not in core_src, "ToolsCore symbol must be fully removed"
