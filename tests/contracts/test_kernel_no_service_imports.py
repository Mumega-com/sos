"""Contract tests enforcing the R0 floor: sos/kernel/**/*.py must never import sos.services.*."""
from __future__ import annotations

import ast
from pathlib import Path


_ALLOWED_EXCEPTIONS: frozenset[str] = frozenset()


def _kernel_root() -> Path:
    return Path(__file__).resolve().parents[2] / "sos" / "kernel"


def _collect_kernel_files() -> list[Path]:
    return [
        p
        for p in _kernel_root().rglob("*.py")
        if "__pycache__" not in p.parts
    ]


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


def test_all_kernel_files_parse() -> None:
    """Sanity check: all kernel .py files exist and are valid Python."""
    files = _collect_kernel_files()
    assert files, f"No .py files found under {_kernel_root()}"
    for p in files:
        try:
            ast.parse(p.read_text())
        except SyntaxError as exc:
            raise AssertionError(f"Syntax error in {p}: {exc}") from exc


def test_no_kernel_file_imports_services() -> None:
    """R0 floor: zero sos.services.* imports across ALL kernel files, no exceptions."""
    leaks: list[tuple[Path, int, str]] = []
    for p in _collect_kernel_files():
        tree = ast.parse(p.read_text())
        for lineno, mod in _collect_service_imports(tree):
            leaks.append((p, lineno, mod))

    assert leaks == [], (
        "R0 floor violation — sos/kernel/ files must NEVER import sos.services.*:\n"
        + "\n".join(f"  {p} line {lineno}: {mod}" for p, lineno, mod in leaks)
    )


def test_zero_exceptions_policy() -> None:
    """The allowed-exception set must be empty — any addition is a visible policy change."""
    assert len(_ALLOWED_EXCEPTIONS) == 0, (
        "R0 contract: _ALLOWED_EXCEPTIONS must stay empty. "
        f"Current entries: {_ALLOWED_EXCEPTIONS}"
    )
