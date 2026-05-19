"""Tests for governance policy — sos:policy:governance Redis key.

Covers: write (governance token / Loom in v1), write (wrong token → 403),
read endpoint, and apply_caps() logic.

The first three tests exercise the HTTP endpoints via TestClient.
The last test exercises apply_caps() in isolation (no Redis needed).
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# apply_caps unit tests (no Redis, no HTTP)
# ---------------------------------------------------------------------------

from sos.services.engine.policy import (
    GovernancePolicy,
    SquadPolicy,
    apply_caps,
    FUEL_GRADE_ORDER,
)


def _policy(
    global_max_grade: str | None = None,
    global_max_budget: int | None = None,
    squad_overrides: dict | None = None,
) -> GovernancePolicy:
    return GovernancePolicy(
        version=1,
        updated_at="2026-04-24T00:00:00Z",
        updated_by="loom",
        **{"global": SquadPolicy(max_fuel_grade=global_max_grade, max_token_budget=global_max_budget)},
        squads={k: SquadPolicy(**v) for k, v in (squad_overrides or {}).items()},
    )


def test_apply_caps_caps_fuel_grade_above_max():
    """Task with aviation grade is capped to regular when policy allows only regular."""
    task = {"id": "t1", "squad_id": "sq-a", "token_budget": 1000, "inputs": {"fuel_grade": "aviation"}}
    capped = apply_caps(task, _policy(global_max_grade="regular"))
    assert capped["inputs"]["fuel_grade"] == "regular"


def test_apply_caps_does_not_change_grade_below_max():
    """Task already within allowed grade is unchanged."""
    task = {"id": "t2", "squad_id": "sq-b", "token_budget": 500, "inputs": {"fuel_grade": "diesel"}}
    capped = apply_caps(task, _policy(global_max_grade="aviation"))
    assert capped["inputs"]["fuel_grade"] == "diesel"


def test_apply_caps_caps_token_budget():
    """token_budget above max is capped."""
    task = {"id": "t3", "squad_id": "sq-c", "token_budget": 80000, "inputs": {}}
    capped = apply_caps(task, _policy(global_max_budget=50000))
    assert capped["token_budget"] == 50000


def test_apply_caps_squad_override_beats_global():
    """Squad-level max_fuel_grade takes precedence over global."""
    task = {"id": "t4", "squad_id": "sq-strict", "token_budget": 1000, "inputs": {"fuel_grade": "supernova"}}
    policy = _policy(
        global_max_grade="aviation",
        squad_overrides={"sq-strict": {"max_fuel_grade": "diesel"}},
    )
    capped = apply_caps(task, policy)
    assert capped["inputs"]["fuel_grade"] == "diesel"


def test_apply_caps_does_not_mutate_original():
    """apply_caps returns a copy — original task dict is not modified."""
    task = {"id": "t5", "squad_id": "sq-d", "token_budget": 5000, "inputs": {"fuel_grade": "supernova"}}
    original_grade = task["inputs"]["fuel_grade"]
    apply_caps(task, _policy(global_max_grade="diesel"))
    assert task["inputs"]["fuel_grade"] == original_grade  # original unchanged


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

GOVERNANCE_TOKEN = "sk-bus-loom-test-token"
WRONG_TOKEN = "sk-wrong-token"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient for SOS Engine app with Redis mocked out."""
    monkeypatch.setenv("SOS_GOVERNANCE_POLICY_TOKEN", GOVERNANCE_TOKEN)

    # Patch Redis so engine/app.py doesn't need a real Redis connection
    fake_store: dict[str, str] = {}

    fake_redis = MagicMock()
    fake_redis.get.side_effect = lambda key: fake_store.get(key, None)
    fake_redis.set.side_effect = lambda key, value: fake_store.update({key: value})

    import sos.services.engine.policy as policy_mod
    monkeypatch.setattr(policy_mod, "_cached_policy", None)
    monkeypatch.setattr(policy_mod, "_cached_at", 0.0)

    import sos.services.engine.app as engine_app
    monkeypatch.setattr(engine_app, "_get_redis", lambda: fake_redis)

    from fastapi.testclient import TestClient
    return TestClient(engine_app.app, raise_server_exceptions=False)


def _governance_headers(token: str = GOVERNANCE_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


VALID_POLICY = {
    "version": 1,
    "updated_at": "2026-04-24T00:00:00Z",
    "updated_by": "loom",
    "global": {"max_fuel_grade": "aviation", "max_token_budget": 50000},
    "squads": {},
}


def test_write_governance_policy_with_valid_token(client):
    """PUT /policy/governance succeeds with correct governance token."""
    r = client.put("/policy/governance", json=VALID_POLICY, headers=_governance_headers())
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"


def test_write_governance_policy_rejected_with_wrong_token(client):
    """PUT /policy/governance returns 403 with wrong token."""
    r = client.put("/policy/governance", json=VALID_POLICY, headers=_governance_headers(WRONG_TOKEN))
    assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"


def test_read_governance_policy(client):
    """GET /policy/governance returns the stored policy after a write."""
    client.put("/policy/governance", json=VALID_POLICY, headers=_governance_headers())
    r = client.get("/policy/governance", headers=_governance_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == 1
    assert data["updated_by"] == "loom"
    assert data["global"]["max_fuel_grade"] == "aviation"
