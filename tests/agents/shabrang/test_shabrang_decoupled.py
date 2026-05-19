"""v0.4.6 Step 3 — P1-04 close.

Before: sos.agents.shabrang.agent imported
sos.services.engine.core.SOSEngine (R2 violation).

After: ShabrangAgent is a plain class. Its public surface (Config,
CoherencePhysics, MirrorClient) is kernel + client only. The SOSEngine
inheritance was dead weight — Shabrang never chats, only mines latency.
"""
from __future__ import annotations

import ast
from pathlib import Path

AGENT_FILE = Path(__file__).resolve().parents[3] / "sos" / "agents" / "shabrang" / "agent.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for n in node.names:
                mods.add(f"{node.module}.{n.name}")
    return mods


def test_shabrang_agent_does_not_import_service_engine():
    mods = _imported_modules(AGENT_FILE)
    leaks = [m for m in mods if m.startswith("sos.services.engine")]
    assert leaks == [], f"agents/shabrang/agent.py still reaches into engine: {leaks}"


def test_shabrang_agent_no_longer_inherits_sosengine():
    tree = ast.parse(AGENT_FILE.read_text(encoding="utf-8"))
    agent_cls = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ShabrangAgent"),
        None,
    )
    assert agent_cls is not None, "ShabrangAgent class not found"
    base_names = []
    for b in agent_cls.bases:
        if isinstance(b, ast.Name):
            base_names.append(b.id)
        elif isinstance(b, ast.Attribute):
            base_names.append(b.attr)
    assert "SOSEngine" not in base_names, f"ShabrangAgent still inherits: {base_names}"


def test_shabrang_class_importable_and_constructs():
    # Plain import — no heavy engine stack, no LLM SDK requirement.
    from sos.agents.shabrang.agent import ShabrangAgent

    agent = ShabrangAgent()
    assert agent.agent_name == "shabrang_squad"
    assert agent.running is False  # not started yet
    assert agent.is_mining is False
    # Sanity: kernel/client handles wired.
    assert agent.physics is not None
    assert agent.mirror is not None
