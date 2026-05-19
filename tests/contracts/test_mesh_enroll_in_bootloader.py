"""Contract test: the agent bootloader must call /mesh/enroll.

Phase 3 (v0.9.2) established that every agent onboarded via
AgentJoinService.join() enrolls into the mesh registry. This test
ensures the call is not silently removed. See
docs/plans/2026-04-19-phase-3-mesh-enrollment.md W6 for context.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JOIN_PATH = REPO_ROOT / "sos" / "agents" / "join.py"


def _find_join_method(tree: ast.AST) -> ast.AsyncFunctionDef:
    """Return the AgentJoinService.join AsyncFunctionDef node, or raise."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentJoinService":
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == "join":
                    return child
    raise AssertionError(
        "AgentJoinService.join not found in sos/agents/join.py — "
        "the canonical bootloader has been renamed or moved"
    )


def _references_mesh_enroll(node: ast.AST) -> bool:
    """True if any descendant calls enroll_mesh() or mentions '/mesh/enroll'."""
    for descendant in ast.walk(node):
        if isinstance(descendant, ast.Call):
            func = descendant.func
            if isinstance(func, ast.Attribute) and func.attr == "enroll_mesh":
                return True
        if isinstance(descendant, ast.Constant) and descendant.value == "/mesh/enroll":
            return True
    return False


def test_join_module_exists() -> None:
    """Sanity: the file the contract test targets actually exists."""
    assert JOIN_PATH.exists(), (
        f"sos/agents/join.py not found at {JOIN_PATH} — file moved or deleted; "
        "update this contract test to point at the new location."
    )


def test_join_calls_mesh_enroll() -> None:
    source = JOIN_PATH.read_text()
    tree = ast.parse(source)
    join_fn = _find_join_method(tree)
    assert _references_mesh_enroll(join_fn), (
        "AgentJoinService.join() no longer calls enroll_mesh() or references\n"
        "/mesh/enroll. This breaks the Phase 3 invariant that every onboarded\n"
        "agent lands in the mesh registry. Restore the call (see commit\n"
        "3595fde5 for the canonical pattern)."
    )
