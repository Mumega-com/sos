"""v0.4.6 Step 8 — R2 structural sweep.

Walk every .py file under sos/clients/ and assert that none of them imports
anything from sos.services.*. Clients are HTTP proxies; any service import
is a boundary violation that eventually gets flagged by import-linter's R2
contract, but this test catches regressions at PR review time without
needing to run lint-imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "sos" / "clients"


def _service_imports(file_path: Path) -> list[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    leaks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("sos.services"):
                leaks.append(f"{file_path.name}: from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sos.services"):
                    leaks.append(f"{file_path.name}: import {alias.name}")
    return leaks


def test_no_client_imports_any_service_module():
    all_leaks: list[str] = []
    for py in sorted(CLIENTS_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        all_leaks.extend(_service_imports(py))
    assert all_leaks == [], (
        "sos/clients/ must not import sos.services.*. "
        "Violations found:\n  " + "\n  ".join(all_leaks)
    )


def test_clients_init_does_not_import_services():
    init = CLIENTS_DIR / "__init__.py"
    leaks = _service_imports(init)
    assert leaks == [], f"sos/clients/__init__.py leaks into services: {leaks}"
