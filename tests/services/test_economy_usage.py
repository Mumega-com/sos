"""End-to-end tests for trop #98 — POST /usage ingest endpoint.

Verifies:
  - UsageLog append + read round-trip
  - POST /usage with valid bearer token writes to log and returns 201 + id
  - Missing/invalid bearer → 401
  - Cross-tenant write attempt → 403
  - System-scoped token can write for any tenant
  - Flat-billed image event (image_count>0, cost_micros set) round-trips
  - GET /usage filters by tenant scope
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    """Patch the tokens.json the economy app reads (via sos.services.auth)."""
    tokens = [
        {"label": "trop-tenant", "token": "tk_trop_raw", "project": "therealmofpatterns", "active": True},
        {"label": "trop-hashed", "token_hash": hashlib.sha256(b"tk_trop_hashed").hexdigest(),
         "project": "therealmofpatterns", "active": True},
        {"label": "dnu-tenant", "token": "tk_dnu_raw", "project": "dnu", "active": True},
        {"label": "system-admin", "token": "tk_system_raw", "active": True},  # no project → system scope
        {"label": "inactive", "token": "tk_inactive", "project": "x", "active": False},
    ]
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(tokens))
    # Economy now delegates to sos.services.auth — patch TOKENS_PATH there.
    import sos.services.auth as auth_mod
    monkeypatch.setattr(auth_mod, "TOKENS_PATH", p)
    auth_mod._cache.invalidate()
    return p


@pytest.fixture
def usage_log_path(tmp_path, monkeypatch):
    """Redirect the usage log to a temp JSONL file."""
    p = tmp_path / "usage_events.jsonl"
    monkeypatch.setenv("SOS_USAGE_LOG_PATH", str(p))
    # Rebuild the module-level log with the new path.
    import sos.services.economy.app as app_mod
    from sos.services.economy.usage_log import UsageLog
    monkeypatch.setattr(app_mod, "_usage_log", UsageLog())
    return p


@pytest.fixture
def client(tokens_file, usage_log_path):
    from sos.services.economy.app import app
    return TestClient(app)


@pytest.fixture
def valid_event():
    return {
        "tenant": "therealmofpatterns",
        "provider": "google",
        "model": "gemini-flash-lite-latest",
        "endpoint": "/api/archetype-report",
        "input_tokens": 152,
        "output_tokens": 68,
        "cost_micros": 6200,
        "metadata": {"report_id": "e3921c677f915013"},
    }


class TestUsageLogUnit:
    def test_append_read_roundtrip(self, tmp_path):
        from sos.services.economy.usage_log import UsageEvent, UsageLog
        log = UsageLog(path=tmp_path / "events.jsonl")
        event = UsageEvent(tenant="x", provider="google", model="m", cost_micros=10)
        stored = log.append(event)
        assert stored.id
        assert stored.received_at
        back = log.read_all(tenant="x")
        assert len(back) == 1
        assert back[0].cost_micros == 10
        assert back[0].tenant == "x"

    def test_tenant_filter(self, tmp_path):
        from sos.services.economy.usage_log import UsageEvent, UsageLog
        log = UsageLog(path=tmp_path / "events.jsonl")
        log.append(UsageEvent(tenant="a", provider="p", model="m"))
        log.append(UsageEvent(tenant="b", provider="p", model="m"))
        log.append(UsageEvent(tenant="a", provider="p", model="m"))
        assert log.count(tenant="a") == 2
        assert log.count(tenant="b") == 1

    def test_limit(self, tmp_path):
        from sos.services.economy.usage_log import UsageEvent, UsageLog
        log = UsageLog(path=tmp_path / "events.jsonl")
        for i in range(5):
            log.append(UsageEvent(tenant="x", provider="p", model="m", input_tokens=i))
        back = log.read_all(tenant="x", limit=2)
        # newest 2 (last appended)
        assert len(back) == 2
        assert [e.input_tokens for e in back] == [3, 4]


class TestAuth:
    def test_missing_bearer_returns_401(self, client, valid_event):
        resp = client.post("/usage", json=valid_event)
        assert resp.status_code == 401

    def test_invalid_bearer_returns_401(self, client, valid_event):
        resp = client.post("/usage", json=valid_event, headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_inactive_token_returns_401(self, client, valid_event):
        resp = client.post("/usage", json=valid_event, headers={"Authorization": "Bearer tk_inactive"})
        assert resp.status_code == 401

    def test_valid_raw_token_works(self, client, valid_event):
        resp = client.post("/usage", json=valid_event, headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 201, resp.text

    def test_valid_hashed_token_works(self, client, valid_event):
        resp = client.post("/usage", json=valid_event, headers={"Authorization": "Bearer tk_trop_hashed"})
        assert resp.status_code == 201, resp.text


class TestTenantScoping:
    def test_tenant_scope_enforced(self, client, valid_event):
        # trop token tries to write an event claiming to be dnu's.
        bad = dict(valid_event)
        bad["tenant"] = "dnu"
        resp = client.post("/usage", json=bad, headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 403
        assert "therealmofpatterns" in resp.json()["detail"]

    def test_system_token_can_write_any_tenant(self, client, valid_event):
        # System-scoped token (no project field) is unrestricted.
        resp = client.post("/usage", json=valid_event, headers={"Authorization": "Bearer tk_system_raw"})
        assert resp.status_code == 201

    def test_correct_scope_works(self, client, valid_event):
        resp = client.post("/usage", json=valid_event, headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 201


class TestPayloadValidation:
    def test_missing_tenant_returns_422(self, client):
        resp = client.post("/usage", json={"provider": "google", "model": "x"},
                           headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 422

    def test_negative_tokens_rejected(self, client):
        body = {"tenant": "therealmofpatterns", "provider": "google", "model": "x", "input_tokens": -5}
        resp = client.post("/usage", json=body, headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 422


class TestFlatPricedImageEvent:
    def test_imagen_4_event_roundtrip(self, client, tmp_path, monkeypatch):
        body = {
            "tenant": "therealmofpatterns",
            "provider": "google",
            "model": "imagen-4.0-generate-001",
            "endpoint": "/api/birth-chart-image",
            "image_count": 3,
            "cost_micros": 120_000,  # 3 × 4 cents × 10_000 micros
            "cost_currency": "USD",
        }
        resp = client.post("/usage", json=body, headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 201
        event_id = resp.json()["id"]

        # Read it back
        resp2 = client.get("/usage", headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp2.status_code == 200
        events = resp2.json()["events"]
        match = [e for e in events if e["id"] == event_id]
        assert match
        assert match[0]["image_count"] == 3
        assert match[0]["cost_micros"] == 120_000


class TestGetUsage:
    def test_tenant_sees_own_events_only(self, client, valid_event):
        # trop writes
        client.post("/usage", json=valid_event, headers={"Authorization": "Bearer tk_trop_raw"})
        # dnu writes
        dnu_event = dict(valid_event)
        dnu_event["tenant"] = "dnu"
        dnu_event["metadata"] = {"report_id": "dnu-1"}
        client.post("/usage", json=dnu_event, headers={"Authorization": "Bearer tk_dnu_raw"})

        # trop reads — should see only trop events
        resp = client.get("/usage", headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 200
        events = resp.json()["events"]
        tenants = {e["tenant"] for e in events}
        assert tenants == {"therealmofpatterns"}

    def test_system_token_sees_all_if_no_filter(self, client, valid_event):
        client.post("/usage", json=valid_event, headers={"Authorization": "Bearer tk_trop_raw"})
        dnu = dict(valid_event)
        dnu["tenant"] = "dnu"
        client.post("/usage", json=dnu, headers={"Authorization": "Bearer tk_dnu_raw"})

        # System token reads without filter — returns all
        resp = client.get("/usage", headers={"Authorization": "Bearer tk_system_raw"})
        assert resp.status_code == 200
        tenants = {e["tenant"] for e in resp.json()["events"]}
        assert tenants >= {"therealmofpatterns", "dnu"}

    def test_tenant_cross_query_forbidden(self, client):
        # trop trying to read dnu
        resp = client.get("/usage?tenant=dnu", headers={"Authorization": "Bearer tk_trop_raw"})
        assert resp.status_code == 403
