"""v0.4.6 Steps 4+5 — P1-05 close (agents/join half).

Before: sos.agents.join imported sos.services.squad.auth.SYSTEM_TOKEN and
sos.services.journeys.tracker.JourneyTracker in-process (two R2 leaks).

After: SYSTEM_TOKEN resolution is inlined from env; JourneyTracker use is
replaced with the HTTP AsyncJourneysClient.
"""
from __future__ import annotations

import ast
from pathlib import Path

JOIN_FILE = Path(__file__).resolve().parents[2] / "sos" / "agents" / "join.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for n in node.names:
                mods.add(f"{node.module}.{n.name}")
    return mods


def test_join_does_not_import_squad_service():
    mods = _imported_modules(JOIN_FILE)
    leaks = [m for m in mods if m.startswith("sos.services.squad")]
    assert leaks == [], f"agents/join.py still reaches into squad: {leaks}"


def test_join_does_not_import_journeys_service():
    mods = _imported_modules(JOIN_FILE)
    leaks = [m for m in mods if m.startswith("sos.services.journeys")]
    assert leaks == [], f"agents/join.py still reaches into journeys: {leaks}"


def test_join_uses_async_journeys_client():
    src = JOIN_FILE.read_text(encoding="utf-8")
    assert "AsyncJourneysClient" in src
    assert "from sos.clients.journeys import AsyncJourneysClient" in src


def test_admin_token_resolves_from_env(monkeypatch):
    from sos.agents.join import _get_admin_token

    monkeypatch.delenv("SOS_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-abc")
    assert _get_admin_token() == "sys-abc"

    monkeypatch.setenv("SOS_ADMIN_TOKEN", "admin-xyz")
    assert _get_admin_token() == "admin-xyz"
