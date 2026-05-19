"""v0.4.5 Wave 6 — dashboard → economy / registry decoupling (P0-12).

Dashboard reads usage events through sos.clients.economy.EconomyClient and
agent rosters through sos.clients.registry.RegistryClient — never via direct
imports of sos.services.economy.* or sos.services.registry.*.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sos.contracts.economy import UsageEvent
from sos.kernel.identity import AgentIdentity

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "sos" / "services" / "dashboard"


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


def test_dashboard_does_not_import_service_economy():
    mods = _imported_modules_in_tree(DASHBOARD_DIR)
    leaks = [m for m in mods if m.startswith("sos.services.economy")]
    assert leaks == [], f"dashboard still imports sos.services.economy: {leaks}"


def test_dashboard_does_not_import_service_registry():
    mods = _imported_modules_in_tree(DASHBOARD_DIR)
    leaks = [m for m in mods if m.startswith("sos.services.registry")]
    assert leaks == [], f"dashboard still imports sos.services.registry: {leaks}"


def test_list_usage_returns_typed_usage_events(monkeypatch: pytest.MonkeyPatch):
    """EconomyClient.list_usage deserializes into UsageEvent instances."""
    from sos.clients.economy import EconomyClient

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "events": [
            {
                "id": "evt-1",
                "tenant": "acme",
                "provider": "google",
                "model": "gemini-flash",
                "cost_micros": 1234,
                "occurred_at": "2026-04-18T00:00:00Z",
                "received_at": "2026-04-18T00:00:01Z",
                "input_tokens": 100,
                "output_tokens": 200,
            }
        ],
        "count": 1,
    }
    client = EconomyClient(base_url="http://localhost:9999")
    with patch.object(client, "_request", return_value=fake_response) as mock_req:
        events = client.list_usage(tenant="acme", limit=5)

    mock_req.assert_called_once()
    assert mock_req.call_args.args[0] == "GET"
    assert "tenant=acme" in mock_req.call_args.args[1]
    assert "limit=5" in mock_req.call_args.args[1]
    assert len(events) == 1
    assert isinstance(events[0], UsageEvent)
    assert events[0].cost_micros == 1234
    assert events[0].tenant == "acme"


def test_list_usage_filters_unknown_fields(monkeypatch: pytest.MonkeyPatch):
    from sos.clients.economy import EconomyClient

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "events": [
            {
                "id": "evt-2",
                "tenant": "acme",
                "cost_micros": 0,
                "weird_unknown_field": "ignored",
            }
        ]
    }
    client = EconomyClient(base_url="http://localhost:9999")
    with patch.object(client, "_request", return_value=fake_response):
        events = client.list_usage()

    assert len(events) == 1
    assert isinstance(events[0], UsageEvent)


def test_dashboard_tenants_uses_economy_client(monkeypatch: pytest.MonkeyPatch):
    """_tenant_skills_and_usage routes usage reads through EconomyClient."""
    from sos.services.dashboard import tenants as tenants_mod

    monkeypatch.setenv("SOS_ECONOMY_URL", "http://fake-economy:6062")

    fake_events = [
        UsageEvent(
            id="e1",
            tenant="acme",
            model="gemini-flash",
            endpoint="/chat",
            cost_micros=500,
            occurred_at="2026-04-18T00:00:00Z",
        )
    ]

    with patch("sos.clients.economy.EconomyClient") as MockClient:
        instance = MockClient.return_value
        instance.list_usage.return_value = fake_events

        with patch("sos.services.dashboard.tenants._get_redis"):
            out = tenants_mod._tenant_skills_and_usage("acme")

        instance.list_usage.assert_called_once_with(tenant="acme", limit=10)

    # Spend aggregate should reflect the fake event.
    assert out["total_spent_micros"] == 500
    assert out["recent_usage"][0]["cost_micros"] == 500


def test_dashboard_tenants_uses_registry_client(monkeypatch: pytest.MonkeyPatch):
    """_agent_status routes registry reads through RegistryClient."""
    from sos.services.dashboard import tenants as tenants_mod

    monkeypatch.setenv("SOS_REGISTRY_URL", "http://fake-registry:6067")

    fake_ident = AgentIdentity(name="acme-bot")
    fake_ident.metadata["status"] = "online"
    fake_ident.metadata["last_seen"] = "2026-04-18"

    with patch("sos.clients.registry.RegistryClient") as MockClient:
        instance = MockClient.return_value
        instance.list_agents.return_value = [fake_ident]

        with patch("sos.services.dashboard.tenants._get_redis"):
            out = tenants_mod._agent_status("acme")

        instance.list_agents.assert_called_once_with(project="acme")

    assert out["online"] == 1
    assert out["agents"][0]["name"] == "acme-bot"
