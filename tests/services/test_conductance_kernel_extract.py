"""v0.4.5 Wave 8 — conductance extracted to kernel (P0-10 + P0-11).

Before Wave 8, sos.services.feedback.loop and sos.services.journeys.tracker
imported conductance helpers from sos.services.health.calcifer — violating
R1 (services don't import other services).

Wave 8 moves the conductance matrix to sos.kernel.conductance.
calcifer re-exports the same symbols for backward compatibility, but the
canonical import path is the kernel module, and the two leaking services
now use that path.
"""
from __future__ import annotations

import ast
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parents[2] / "sos" / "services"


def _imported_modules(root: Path) -> set[str]:
    mods: set[str] = set()
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
    return mods


def test_feedback_no_longer_imports_health():
    mods = _imported_modules(SERVICES_DIR / "feedback")
    leaks = [m for m in mods if m.startswith("sos.services.health")]
    assert leaks == [], f"feedback still imports health: {leaks}"


def test_journeys_no_longer_imports_health():
    mods = _imported_modules(SERVICES_DIR / "journeys")
    leaks = [m for m in mods if m.startswith("sos.services.health")]
    assert leaks == [], f"journeys still imports health: {leaks}"


def test_kernel_conductance_exposes_expected_api():
    from sos.kernel import conductance as k

    for sym in (
        "CONDUCTANCE_FILE",
        "CONDUCTANCE_GAMMA",
        "CONDUCTANCE_ALPHA",
        "_load_conductance",
        "_save_conductance",
        "conductance_update",
        "conductance_decay",
    ):
        assert hasattr(k, sym), f"kernel.conductance missing {sym}"


def test_calcifer_reexports_conductance_symbols():
    """Backward-compat: calcifer.conductance_* is the same object as kernel's."""
    from sos.kernel.conductance import (
        _load_conductance as k_load,
        conductance_decay as k_decay,
        conductance_update as k_update,
    )
    from sos.services.health.calcifer import (
        _load_conductance as c_load,
        conductance_decay as c_decay,
        conductance_update as c_update,
    )

    assert k_load is c_load
    assert k_decay is c_decay
    assert k_update is c_update


def test_conductance_round_trip(tmp_path, monkeypatch):
    """conductance_update → _load_conductance returns what was written."""
    from sos.kernel import conductance as k

    monkeypatch.setattr(k, "CONDUCTANCE_FILE", tmp_path / "G.json")
    k.conductance_update("agent:acme", "sql", reward=100.0)
    G = k._load_conductance()
    assert "agent:acme" in G
    assert G["agent:acme"]["sql"] > 0


def test_conductance_decay_applies_alpha(tmp_path, monkeypatch):
    from sos.kernel import conductance as k

    monkeypatch.setattr(k, "CONDUCTANCE_FILE", tmp_path / "G.json")
    k.conductance_update("a", "s", reward=100.0)
    before = k._load_conductance()["a"]["s"]
    k.conductance_decay()
    after = k._load_conductance()["a"]["s"]
    assert after < before
    assert abs(after - before * (1 - k.CONDUCTANCE_ALPHA)) < 1e-9
