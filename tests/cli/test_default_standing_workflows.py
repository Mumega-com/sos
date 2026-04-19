"""Tests for Phase 7 Step 7.4 default standing workflows."""
from __future__ import annotations

from sos.cli._default_standing_workflows import default_workflows, ensure_workflows_present


def test_default_workflows_contains_growth_intel_for_slug() -> None:
    wfs = default_workflows("acme")
    assert len(wfs) == 1
    wf = wfs[0]
    assert wf["name"] == "acme-growth-intel"
    assert wf["schedule"] == "0 10 * * *"
    assert wf["steps"] == ["trend-finder", "narrative-synth", "dossier-writer"]
    assert wf["bounty_mind"] == 50
    assert wf["trigger"] == "auto"


def test_ensure_workflows_present_appends_when_missing() -> None:
    data: dict = {"workflows": [{"name": "acme-daily", "steps": []}]}
    ensure_workflows_present(data, "acme")
    names = [w["name"] for w in data["workflows"]]
    assert names == ["acme-daily", "acme-growth-intel"]


def test_ensure_workflows_present_is_idempotent() -> None:
    data: dict = {"workflows": []}
    ensure_workflows_present(data, "acme")
    ensure_workflows_present(data, "acme")  # second call — should not duplicate
    names = [w["name"] for w in data["workflows"]]
    assert names == ["acme-growth-intel"]


def test_ensure_workflows_present_preserves_tenant_customization() -> None:
    custom = {
        "name": "acme-growth-intel",
        "schedule": "0 23 * * *",  # tenant overrode the cron
        "bounty_mind": 999,
    }
    data: dict = {"workflows": [custom]}
    ensure_workflows_present(data, "acme")
    # Tenant's version should still win.
    assert data["workflows"][0] is custom
    assert data["workflows"][0]["schedule"] == "0 23 * * *"
    assert data["workflows"][0]["bounty_mind"] == 999


def test_ensure_workflows_present_creates_workflows_key_if_absent() -> None:
    data: dict = {}
    ensure_workflows_present(data, "acme")
    assert "workflows" in data
    assert len(data["workflows"]) == 1
