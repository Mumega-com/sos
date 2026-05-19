"""Tests for the objectives service FastAPI app.

Mirrors tests/services/test_registry_cards.py patching pattern:
monkeypatches storage functions at the app module level so no live Redis
or gate service is required.

Route ordering regressions are explicitly guarded (see tests near the bottom).
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sos.contracts.objective import Objective
from sos.services.objectives.app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # valid ULID (26 chars, correct charset)


def _make_obj(
    obj_id: str = _VALID_ID,
    title: str = "Test objective",
    state: str = "open",
    project: str | None = None,
    tags: list[str] | None = None,
    bounty: int = 0,
    holder_agent: str | None = None,
) -> Objective:
    now = Objective.now_iso()
    return Objective(
        id=obj_id,
        title=title,
        state=state,  # type: ignore[arg-type]
        created_by="test-agent",
        created_at=now,
        updated_at=now,
        project=project,
        tags=tags or [],
        bounty_mind=bounty,
        holder_agent=holder_agent,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _noop() -> None:  # pragma: no cover
        return None

    monkeypatch.setattr(
        "sos.services.objectives.app._startup",
        _noop,
        raising=True,
    )
    return TestClient(app)


@pytest.fixture
def system_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-sys-token-objectives"
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", token)
    from sos.kernel.auth import get_cache

    get_cache().invalidate()
    return token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# Shared fake for can_execute — always allows
def _make_fake_can_execute():
    from sos.contracts.policy import PolicyDecision

    async def _fake(**kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason="test: gate bypassed",
            tier="act_freely",
            action=kwargs.get("action", ""),
            resource=kwargs.get("resource", ""),
            agent="test-agent",
            tenant="mumega",
            pillars_passed=["system/admin"],
            pillars_failed=[],
            capability_ok=None,
            metadata={},
        )

    return _fake


# ---------------------------------------------------------------------------
# 1. Missing bearer — 401 on every endpoint
# ---------------------------------------------------------------------------


def test_create_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post("/objectives", json={})
    assert resp.status_code == 401


def test_query_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get("/objectives")
    assert resp.status_code == 401


def test_get_one_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get(f"/objectives/{_VALID_ID}")
    assert resp.status_code == 401


def test_get_tree_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get(f"/objectives/{_VALID_ID}/tree")
    assert resp.status_code == 401


def test_claim_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post(f"/objectives/{_VALID_ID}/claim")
    assert resp.status_code == 401


def test_heartbeat_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post(f"/objectives/{_VALID_ID}/heartbeat")
    assert resp.status_code == 401


def test_release_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post(f"/objectives/{_VALID_ID}/release")
    assert resp.status_code == 401


def test_complete_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post(f"/objectives/{_VALID_ID}/complete", json={"artifact_url": "x"})
    assert resp.status_code == 401


def test_ack_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post(f"/objectives/{_VALID_ID}/ack")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Invalid payload — 422
# ---------------------------------------------------------------------------


def test_create_invalid_payload_is_422(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sos.services.objectives.app.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )
    resp = client.post(
        "/objectives",
        json={"nonsense": True},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 422


def test_complete_missing_artifact_url_is_422(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sos.services.objectives.app.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )
    resp = client.post(
        f"/objectives/{_VALID_ID}/complete",
        json={"notes": "no artifact"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3. Create happy path
# ---------------------------------------------------------------------------


def test_create_happy_path(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_write(obj: Objective, ttl_seconds: int | None = None) -> None:
        captured["obj"] = obj

    monkeypatch.setattr(
        "sos.services.objectives.app.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.write_objective",
        fake_write,
        raising=True,
    )

    now = Objective.now_iso()
    payload = {
        "title": "Build the spine",
        "created_by": "codex",
        "created_at": now,
        "updated_at": now,
    }
    resp = client.post(
        "/objectives",
        json=payload,
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Server must generate id, created_at, updated_at
    assert "id" in body and body["id"]
    assert "created_at" in body and body["created_at"]
    assert body["title"] == "Build the spine"
    assert captured.get("obj") is not None


# ---------------------------------------------------------------------------
# 4. Create then GET by id
# ---------------------------------------------------------------------------


def test_create_then_get(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj()
    stored: dict[str, Any] = {}

    def fake_write(o: Objective, ttl_seconds: int | None = None) -> None:
        stored["obj"] = o

    def fake_read(obj_id: str, project: str | None = None) -> Objective | None:
        o = stored.get("obj")
        if o and o.id == obj_id:
            return o
        return None

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr("sos.services.objectives.app.write_objective", fake_write, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.read_objective", fake_read, raising=True)

    now = Objective.now_iso()
    resp = client.post(
        "/objectives",
        json={"id": obj.id, "title": obj.title, "created_by": "codex", "created_at": now, "updated_at": now},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200

    resp2 = client.get(f"/objectives/{obj.id}", headers=_auth_headers(system_token))
    assert resp2.status_code == 200
    assert resp2.json()["id"] == obj.id


# ---------------------------------------------------------------------------
# 5. GET missing id → 404
# ---------------------------------------------------------------------------


def test_get_missing_id_is_404(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.read_objective",
        lambda obj_id, project=None: None,
        raising=True,
    )
    resp = client.get(f"/objectives/{_VALID_ID}", headers=_auth_headers(system_token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. GET tree on missing root → 404
# ---------------------------------------------------------------------------


def test_get_tree_missing_root_is_404(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.read_tree",
        lambda root_id, project=None, max_depth=10: {},
        raising=True,
    )
    resp = client.get(f"/objectives/{_VALID_ID}/tree", headers=_auth_headers(system_token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. GET tree returns nested shape
# ---------------------------------------------------------------------------


def test_get_tree_returns_nested_shape(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _make_obj()
    child_id = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    child = _make_obj(obj_id=child_id, title="Child node")

    def fake_read_tree(root_id: str, project: str | None = None, max_depth: int = 10) -> dict:
        if root_id == root.id:
            return {"objective": root, "children": [{"objective": child, "children": []}]}
        return {}

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr("sos.services.objectives.app.read_tree", fake_read_tree, raising=True)

    resp = client.get(f"/objectives/{root.id}/tree", headers=_auth_headers(system_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["objective"]["id"] == root.id
    assert len(body["children"]) == 1
    assert body["children"][0]["objective"]["id"] == child_id


# ---------------------------------------------------------------------------
# 8. GET /objectives returns list + count
# ---------------------------------------------------------------------------


def test_query_returns_list_and_count(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objs = [_make_obj(), _make_obj(obj_id="01ARZ3NDEKTSV4RRFFQ69G5FAW", title="Second")]

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.query_open",
        lambda project=None, tag=None, min_bounty=None, subtree_root=None, capability=None: objs,
        raising=True,
    )

    resp = client.get("/objectives", headers=_auth_headers(system_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["objectives"]) == 2


# ---------------------------------------------------------------------------
# 9. GET /objectives?tag=foo filters correctly
# ---------------------------------------------------------------------------


def test_query_tag_filter(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_query(
        project=None, tag=None, min_bounty=None, subtree_root=None, capability=None
    ) -> list[Objective]:
        captured["tag"] = tag
        return []

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr("sos.services.objectives.app.query_open", fake_query, raising=True)

    resp = client.get("/objectives?tag=infra", headers=_auth_headers(system_token))
    assert resp.status_code == 200
    assert captured["tag"] == "infra"


# ---------------------------------------------------------------------------
# 10. GET /objectives?min_bounty=100 filters correctly
# ---------------------------------------------------------------------------


def test_query_min_bounty_filter(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_query(
        project=None, tag=None, min_bounty=None, subtree_root=None, capability=None
    ) -> list[Objective]:
        captured["min_bounty"] = min_bounty
        return []

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr("sos.services.objectives.app.query_open", fake_query, raising=True)

    resp = client.get("/objectives?min_bounty=100", headers=_auth_headers(system_token))
    assert resp.status_code == 200
    assert captured["min_bounty"] == 100


# ---------------------------------------------------------------------------
# 11. Claim happy path
# ---------------------------------------------------------------------------


def test_claim_happy_path(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.claim_objective",
        lambda obj_id, agent, project=None: True,
        raising=True,
    )

    resp = client.post(
        f"/objectives/{_VALID_ID}/claim",
        json={"agent": "kasra"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["holder_agent"] == "kasra"
    assert body["obj_id"] == _VALID_ID


# ---------------------------------------------------------------------------
# 12. Claim when already claimed → 409
# ---------------------------------------------------------------------------


def test_claim_already_claimed_is_409(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.claim_objective",
        lambda obj_id, agent, project=None: False,
        raising=True,
    )

    resp = client.post(
        f"/objectives/{_VALID_ID}/claim",
        json={"agent": "kasra"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 13. Heartbeat happy path
# ---------------------------------------------------------------------------


def test_heartbeat_happy_path(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.heartbeat_objective",
        lambda obj_id, project=None: True,
        raising=True,
    )

    resp = client.post(f"/objectives/{_VALID_ID}/heartbeat", headers=_auth_headers(system_token))
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# 14. Heartbeat on missing id → 404
# ---------------------------------------------------------------------------


def test_heartbeat_missing_is_404(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.heartbeat_objective",
        lambda obj_id, project=None: False,
        raising=True,
    )

    resp = client.post(f"/objectives/{_VALID_ID}/heartbeat", headers=_auth_headers(system_token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 15. Release happy path
# ---------------------------------------------------------------------------


def test_release_happy_path(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.release_objective",
        lambda obj_id, project=None: True,
        raising=True,
    )

    resp = client.post(f"/objectives/{_VALID_ID}/release", headers=_auth_headers(system_token))
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# 16. Complete happy path → state=shipped
# ---------------------------------------------------------------------------


def test_complete_happy_path(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.complete_objective",
        lambda obj_id, artifact_url, notes="", project=None: True,
        raising=True,
    )

    resp = client.post(
        f"/objectives/{_VALID_ID}/complete",
        json={"artifact_url": "https://s3.example.com/artifact.zip", "notes": "done"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["state"] == "shipped"


# ---------------------------------------------------------------------------
# 17. Complete body missing artifact_url → 422  (already tested above, another variant)
# ---------------------------------------------------------------------------


def test_complete_empty_body_is_422(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    resp = client.post(
        f"/objectives/{_VALID_ID}/complete",
        json={},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 18. Ack happy path returns updated acks list
# ---------------------------------------------------------------------------


def test_ack_happy_path(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj()

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.ack_completion",
        lambda obj_id, acker, project=None: True,
        raising=True,
    )

    acked = obj.model_copy(update={"acks": ["reviewer-agent"]})
    monkeypatch.setattr(
        "sos.services.objectives.app.read_objective",
        lambda obj_id, project=None: acked,
        raising=True,
    )

    resp = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "reviewer-agent"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "reviewer-agent" in body["acks"]


# ---------------------------------------------------------------------------
# 19. Route ordering regression: GET /objectives?tag=x must hit query route
# ---------------------------------------------------------------------------


def test_query_route_not_shadowed_by_obj_id(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /objectives (with query params) must reach query_objectives, not get_objective."""
    called: dict[str, bool] = {"query": False, "read": False}

    def fake_query(project=None, tag=None, min_bounty=None, subtree_root=None, capability=None):
        called["query"] = True
        return []

    def fake_read(obj_id: str, project=None):
        called["read"] = True
        return None

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr("sos.services.objectives.app.query_open", fake_query, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.read_objective", fake_read, raising=True)

    resp = client.get("/objectives?tag=x", headers=_auth_headers(system_token))
    assert resp.status_code == 200
    assert called["query"] is True
    assert called["read"] is False


# ---------------------------------------------------------------------------
# 20. Route ordering regression: POST /objectives/xyz/claim hits claim route
# ---------------------------------------------------------------------------


def test_claim_route_not_shadowed(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, bool] = {"claim": False}

    def fake_claim(obj_id: str, agent: str, project=None) -> bool:
        called["claim"] = True
        return True

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr("sos.services.objectives.app.claim_objective", fake_claim, raising=True)

    resp = client.post(
        f"/objectives/{_VALID_ID}/claim",
        json={"agent": "test-agent"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    assert called["claim"] is True


# ---------------------------------------------------------------------------
# 21. Full flow: create → claim → heartbeat → complete → ack
# ---------------------------------------------------------------------------


def test_full_flow(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store: dict[str, Any] = {}

    def fake_write(obj: Objective, ttl_seconds=None) -> None:
        store["obj"] = obj

    def fake_read(obj_id: str, project=None) -> Objective | None:
        o = store.get("obj")
        if o and o.id == obj_id:
            # Return a version with acks if present
            return store.get("acked_obj", o)
        return None

    def fake_claim(obj_id: str, agent: str, project=None) -> bool:
        return True

    def fake_heartbeat(obj_id: str, project=None) -> bool:
        return True

    def fake_complete(obj_id: str, artifact_url: str, notes: str = "", project=None) -> bool:
        return True

    def fake_ack(obj_id: str, acker: str, project=None) -> bool:
        obj = store.get("obj")
        if obj:
            store["acked_obj"] = obj.model_copy(update={"acks": [acker]})
        return True

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr("sos.services.objectives.app.write_objective", fake_write, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.read_objective", fake_read, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.claim_objective", fake_claim, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.heartbeat_objective", fake_heartbeat, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.complete_objective", fake_complete, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.ack_completion", fake_ack, raising=True)

    now = Objective.now_iso()

    # Step 1: create
    r1 = client.post(
        "/objectives",
        json={"id": _VALID_ID, "title": "Full flow", "created_by": "codex", "created_at": now, "updated_at": now},
        headers=_auth_headers(system_token),
    )
    assert r1.status_code == 200

    # Step 2: claim
    r2 = client.post(
        f"/objectives/{_VALID_ID}/claim",
        json={"agent": "kasra"},
        headers=_auth_headers(system_token),
    )
    assert r2.status_code == 200
    assert r2.json()["holder_agent"] == "kasra"

    # Step 3: heartbeat
    r3 = client.post(f"/objectives/{_VALID_ID}/heartbeat", headers=_auth_headers(system_token))
    assert r3.status_code == 200

    # Step 4: complete
    r4 = client.post(
        f"/objectives/{_VALID_ID}/complete",
        json={"artifact_url": "https://example.com/done.zip"},
        headers=_auth_headers(system_token),
    )
    assert r4.status_code == 200
    assert r4.json()["state"] == "shipped"

    # Step 5: ack
    r5 = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "mumega"},
        headers=_auth_headers(system_token),
    )
    assert r5.status_code == 200
    assert r5.json()["ok"] is True
    assert "mumega" in r5.json()["acks"]


# ---------------------------------------------------------------------------
# Step 6 — Scope enforcement tests
# ---------------------------------------------------------------------------


def _make_scoped_viamar_verify():
    """Return a fake _auth_verify_bearer that yields a viamar-scoped context."""
    from sos.kernel.auth import AuthContext

    def fake_verify(authz: str | None) -> AuthContext | None:
        if authz and "scoped-viamar" in authz:
            return AuthContext(
                agent="viamar-agent",
                project="viamar",
                tenant_slug="mumega",
                is_system=False,
                is_admin=False,
                label="scoped",
            )
        return None

    return fake_verify


def test_scoped_token_cross_project_query_is_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoped viamar token querying project=dentalnearyou must get 403."""
    from sos.contracts.policy import PolicyDecision

    async def fake_can_execute(**kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason="test: gate bypassed",
            tier="act_freely",
            action=kwargs.get("action", ""),
            resource=kwargs.get("resource", ""),
            agent="viamar-agent",
            tenant="mumega",
            pillars_passed=["tenant_scope"],
            pillars_failed=[],
            capability_ok=None,
            metadata={},
        )

    monkeypatch.setattr(
        "sos.services.objectives.app._auth_verify_bearer",
        _make_scoped_viamar_verify(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.can_execute",
        fake_can_execute,
        raising=True,
    )

    resp = client.get(
        "/objectives?project=dentalnearyou",
        headers={"Authorization": "Bearer scoped-viamar"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "viamar" in detail
    assert "dentalnearyou" in detail


def test_scoped_token_forced_to_own_project(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoped viamar token with no project param → storage called with project='viamar'."""
    from sos.contracts.policy import PolicyDecision

    observed: dict[str, Any] = {}

    async def fake_can_execute(**kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason="test: gate bypassed",
            tier="act_freely",
            action=kwargs.get("action", ""),
            resource=kwargs.get("resource", ""),
            agent="viamar-agent",
            tenant="mumega",
            pillars_passed=["tenant_scope"],
            pillars_failed=[],
            capability_ok=None,
            metadata={},
        )

    def fake_query(
        project=None, tag=None, min_bounty=None, subtree_root=None, capability=None
    ) -> list[Objective]:
        observed["project"] = project
        return []

    monkeypatch.setattr(
        "sos.services.objectives.app._auth_verify_bearer",
        _make_scoped_viamar_verify(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.can_execute",
        fake_can_execute,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.query_open",
        fake_query,
        raising=True,
    )

    resp = client.get(
        "/objectives",
        headers={"Authorization": "Bearer scoped-viamar"},
    )
    assert resp.status_code == 200
    assert observed["project"] == "viamar"


def test_scoped_token_cross_project_body_create_is_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /objectives with body project='dentalnearyou' and viamar-scoped auth → 403."""
    from sos.contracts.policy import PolicyDecision

    async def fake_can_execute(**kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason="test: gate bypassed",
            tier="act_freely",
            action=kwargs.get("action", ""),
            resource=kwargs.get("resource", ""),
            agent="viamar-agent",
            tenant="mumega",
            pillars_passed=["tenant_scope"],
            pillars_failed=[],
            capability_ok=None,
            metadata={},
        )

    monkeypatch.setattr(
        "sos.services.objectives.app._auth_verify_bearer",
        _make_scoped_viamar_verify(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.can_execute",
        fake_can_execute,
        raising=True,
    )

    now = Objective.now_iso()
    resp = client.post(
        "/objectives",
        json={
            "title": "cross-project attempt",
            "created_by": "viamar-agent",
            "created_at": now,
            "updated_at": now,
            "project": "dentalnearyou",
        },
        headers={"Authorization": "Bearer scoped-viamar"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "dentalnearyou" in detail or "viamar" in detail


# ---------------------------------------------------------------------------
# Step 7 — Audit emission tests
# ---------------------------------------------------------------------------


def test_audit_emits_on_claim(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim route emits objective.state_changed envelope with correct fields."""
    emitted: list[dict[str, Any]] = []

    def fake_emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.claim_objective",
        lambda obj_id, agent, project=None: True,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app._emit_audit",
        fake_emit,
        raising=True,
    )

    resp = client.post(
        f"/objectives/{_VALID_ID}/claim",
        json={"agent": "kasra"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["id"] == _VALID_ID
    assert ev["prior_state"] == "open"
    assert ev["new_state"] == "claimed"
    assert ev["holder"] == "kasra"


def test_audit_failure_does_not_fail_request(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bus failure in _emit_audit must not cause the HTTP request to fail."""

    def exploding_emit(payload: dict[str, Any]) -> None:
        raise RuntimeError("bus is down")

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.claim_objective",
        lambda obj_id, agent, project=None: True,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app._emit_audit",
        exploding_emit,
        raising=True,
    )

    resp = client.post(
        f"/objectives/{_VALID_ID}/claim",
        json={"agent": "kasra"},
        headers=_auth_headers(system_token),
    )
    # Request must succeed even though bus exploded
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_ack_triggers_paid_transition_when_enough_acks(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When check_completion returns a paid Objective, ack response includes state='paid'."""
    from sos.services.objectives import gate as _gate_module

    obj_shipped = _make_obj(state="shipped")
    obj_shipped_with_acks = obj_shipped.model_copy(update={"acks": ["reviewer-agent"]})
    obj_paid = obj_shipped_with_acks.model_copy(update={"state": "paid"})

    async def fake_check_completion(obj_id: str, *, project: str | None = None, **kwargs: Any) -> Objective:
        return obj_paid

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.ack_completion",
        lambda obj_id, acker, project=None: True,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.read_objective",
        lambda obj_id, project=None: obj_shipped_with_acks,
        raising=True,
    )
    monkeypatch.setattr(_gate_module, "check_completion", fake_check_completion, raising=True)

    resp = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "reviewer-agent"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["state"] == "paid"


def test_ack_read_back_failure_returns_graceful_fallback(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If read_objective raises after ack_completion succeeds, return graceful fallback.

    Simulates a Redis flake between the write and the read-back.  The handler
    must NOT propagate the exception as a 500; instead it returns:
      {"ok": True, "acks": [acker], "state": "shipped"}
    """
    from sos.services.objectives import gate as _gate_module
    import redis as _redis_mod

    def fake_read_raises(obj_id: str, project: str | None = None):
        raise _redis_mod.RedisError("connection lost during read-back")

    async def fake_check_completion(obj_id: str, *, project: str | None = None, **kwargs: Any) -> None:
        return None  # gate does not fire

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.ack_completion",
        lambda obj_id, acker, project=None: True,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.read_objective",
        fake_read_raises,
        raising=True,
    )
    monkeypatch.setattr(_gate_module, "check_completion", fake_check_completion, raising=True)

    resp = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "fallback-reviewer"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "fallback-reviewer" in body["acks"]
    assert body["state"] == "shipped"


def test_audit_on_create(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /objectives emits envelope with prior_state=None and new_state='open'."""
    emitted: list[dict[str, Any]] = []

    def fake_emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.write_objective",
        lambda obj, ttl_seconds=None: None,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app._emit_audit",
        fake_emit,
        raising=True,
    )

    now = Objective.now_iso()
    resp = client.post(
        "/objectives",
        json={"title": "audit test obj", "created_by": "codex", "created_at": now, "updated_at": now},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["prior_state"] is None
    assert ev["new_state"] == "open"


# ---------------------------------------------------------------------------
# v0.8.1 (S3) — outcome_score on ack
# ---------------------------------------------------------------------------


def test_ack_with_outcome_score_stores_it(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /ack with {'outcome_score': 0.85} persists the score on the objective."""
    from sos.services.objectives import gate as _gate_module

    store: dict[str, Any] = {"obj": _make_obj(state="shipped")}

    def fake_write(obj: Objective, ttl_seconds: int | None = None) -> None:
        store["obj"] = obj

    def fake_read(obj_id: str, project: str | None = None) -> Objective | None:
        o = store.get("obj")
        if o and o.id == obj_id:
            return o
        return None

    async def fake_check_completion(obj_id: str, *, project: str | None = None, **kwargs: Any) -> None:
        return None  # gate does not fire — payout stays binary, unchanged by score

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.ack_completion",
        lambda obj_id, acker, project=None: True,
        raising=True,
    )
    monkeypatch.setattr("sos.services.objectives.app.write_objective", fake_write, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.read_objective", fake_read, raising=True)
    monkeypatch.setattr(_gate_module, "check_completion", fake_check_completion, raising=True)

    resp = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "reviewer-agent", "outcome_score": 0.85},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200

    # GET the objective and verify the score is stored.
    resp2 = client.get(f"/objectives/{_VALID_ID}", headers=_auth_headers(system_token))
    assert resp2.status_code == 200
    assert resp2.json()["outcome_score"] == 0.85


def test_ack_without_outcome_score_still_works(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: v0.8.0 clients that ack without outcome_score still succeed."""
    from sos.services.objectives import gate as _gate_module

    store: dict[str, Any] = {"obj": _make_obj(state="shipped")}

    def fake_write(obj: Objective, ttl_seconds: int | None = None) -> None:
        store["obj"] = obj

    def fake_read(obj_id: str, project: str | None = None) -> Objective | None:
        o = store.get("obj")
        if o and o.id == obj_id:
            return o
        return None

    async def fake_check_completion(obj_id: str, *, project: str | None = None, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.ack_completion",
        lambda obj_id, acker, project=None: True,
        raising=True,
    )
    monkeypatch.setattr("sos.services.objectives.app.write_objective", fake_write, raising=True)
    monkeypatch.setattr("sos.services.objectives.app.read_objective", fake_read, raising=True)
    monkeypatch.setattr(_gate_module, "check_completion", fake_check_completion, raising=True)

    resp = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "reviewer-agent"},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Objective outcome_score remains None.
    resp2 = client.get(f"/objectives/{_VALID_ID}", headers=_auth_headers(system_token))
    assert resp2.status_code == 200
    assert resp2.json()["outcome_score"] is None


def test_audit_event_includes_outcome_score_when_provided(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit payload emitted on ack includes outcome_score only when one was submitted."""
    from sos.services.objectives import gate as _gate_module

    emitted: list[dict[str, Any]] = []

    def fake_emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    async def fake_check_completion(obj_id: str, *, project: str | None = None, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("sos.services.objectives.app.can_execute", _make_fake_can_execute(), raising=True)
    monkeypatch.setattr(
        "sos.services.objectives.app.ack_completion",
        lambda obj_id, acker, project=None: True,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.read_objective",
        lambda obj_id, project=None: _make_obj(state="shipped"),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.objectives.app.write_objective",
        lambda obj, ttl_seconds=None: None,
        raising=True,
    )
    monkeypatch.setattr("sos.services.objectives.app._emit_audit", fake_emit, raising=True)
    monkeypatch.setattr(_gate_module, "check_completion", fake_check_completion, raising=True)

    # First: ack WITH score — audit payload must include it.
    resp = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "reviewer-agent", "outcome_score": 0.42},
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    assert len(emitted) == 1
    assert emitted[0].get("outcome_score") == 0.42

    # Second: ack WITHOUT score — key must be absent (not None) in payload.
    emitted.clear()
    resp2 = client.post(
        f"/objectives/{_VALID_ID}/ack",
        json={"acker": "reviewer-agent"},
        headers=_auth_headers(system_token),
    )
    assert resp2.status_code == 200
    assert len(emitted) == 1
    assert "outcome_score" not in emitted[0]
