from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "architecture" / "plugin-boundary.md"
ADAPTER = ROOT / "examples" / "host_profiles" / "openclaw_hermes_adapter.py"


def test_plugin_boundary_doc_names_required_contract_surfaces() -> None:
    text = DOC.read_text(encoding="utf-8")

    for phrase in (
        "Register or announce an agent",
        "Send a message",
        "Read inbox",
        "Wake a local process",
        "Create/list/claim/complete tasks",
        "Host overlays",
        "Compatibility shims",
        "Minimal Smoke Checklist",
    ):
        assert phrase in text


def test_openclaw_hermes_adapter_imports_only_public_sos_sdk() -> None:
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert "sos.sdk" in imports
    assert not any(name.startswith("sos.services") for name in imports)
    assert not any(name.startswith("sos.agents") for name in imports)
    assert not any(name.startswith("mumega") for name in imports)


def test_openclaw_hermes_adapter_profile_smoke(monkeypatch) -> None:
    monkeypatch.setenv("SOS_BUS_TOKEN", "sk-test-token")
    monkeypatch.setenv("SOS_AGENT", "Hermes Worker")
    monkeypatch.setenv("SOS_PROJECT", "sos")
    monkeypatch.setenv("SOS_BRIDGE_URL", "http://localhost:16380")
    monkeypatch.setenv("SOS_SUBSCRIPTIONS", "project:sos:global,squad:research")

    spec = importlib.util.spec_from_file_location("openclaw_hermes_adapter", ADAPTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    profile = module.profile_from_env(runtime="hermes")
    adapter = module.HostRuntimeAdapter(profile)

    assert adapter.profile.agent == "Hermes Worker"
    assert adapter.profile.runtime == "hermes"
    assert adapter.agent.name == "hermes-worker"
    assert adapter.agent.project == "sos"
    assert adapter.health()["status"] == "configured"
