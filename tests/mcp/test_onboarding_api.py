from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

import sos.mcp.sos_mcp_sse as sse


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    tokens = tmp_path / "tokens.json"
    tokens.write_text("[]\n")
    invites = tmp_path / "invites.json"
    invites.write_text("[]\n")
    requests = tmp_path / "requests.json"
    requests.write_text("[]\n")
    monkeypatch.setattr(sse, "BUS_TOKENS_PATH", tokens)
    monkeypatch.setattr(sse, "ONBOARDING_INVITES_PATH", invites)
    monkeypatch.setattr(sse, "ONBOARDING_REQUESTS_PATH", requests)
    sse._local_token_cache.invalidate()
    return TestClient(sse.app)


def test_whoami_without_token_returns_bootstrap_tools(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    resp = client.get("/api/v1/onboarding/whoami")

    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False
    assert {tool["name"] for tool in body["tools"]} == {
        "whoami",
        "login",
        "join_with_invite",
        "register",
    }


def test_public_register_creates_pending_request_not_token(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/v1/onboarding/register",
        json={"slug": "acme", "label": "Acme", "email": "ops@example.com"},
    )

    assert resp.status_code == 202
    assert resp.json()["status"] == "pending_review"
    assert json.loads((tmp_path / "tokens.json").read_text()) == []
    requests = json.loads((tmp_path / "requests.json").read_text())
    assert requests[0]["slug"] == "acme"
    assert requests[0]["status"] == "pending_review"


def test_join_with_invite_mints_tenant_agent_token(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    code = "invite-acme-123"
    (tmp_path / "invites.json").write_text(
        json.dumps(
            [
                {
                    "id": "inv-1",
                    "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                    "tenant_id": "acme",
                    "role": "member",
                    "scopes": ["bus:send", "tasks:*"],
                    "active": True,
                    "max_uses": 1,
                    "uses": 0,
                }
            ]
        )
        + "\n"
    )
    monkeypatch.setattr(sse.requests, "put", lambda *a, **k: None)

    resp = client.post(
        "/api/v1/onboarding/join-with-invite",
        json={"invite_code": code, "agent_name": "athena-acme", "model": "gpt"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "joined"
    assert body["tenant_id"] == "acme"
    assert body["role"] == "member"
    assert body["recovery_guide"] == "~/SOS/docs/agent-onboarding-recovery.md"
    assert body["token"].startswith("sk-agent-acme-athena-acme-")

    records = json.loads((tmp_path / "tokens.json").read_text())
    assert records[0]["project"] == "acme"
    assert records[0]["agent"] == "athena-acme"
    assert records[0]["scope"] == "tenant-agent"
    assert records[0]["role"] == "member"
    assert records[0]["token_hash"] == hashlib.sha256(body["token"].encode()).hexdigest()

    invites = json.loads((tmp_path / "invites.json").read_text())
    assert invites[0]["uses"] == 1
    assert invites[0]["active"] is False


def test_join_with_invite_suffixes_duplicate_agent_name(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    code = "invite-sos-123"
    existing = "sk-agent-sos-hadi-codex-existing"
    (tmp_path / "tokens.json").write_text(
        json.dumps(
            [
                {
                    "token": existing,
                    "token_hash": hashlib.sha256(existing.encode()).hexdigest(),
                    "project": "sos",
                    "agent": "hadi-codex",
                    "scope": "tenant-agent",
                    "role": "member",
                    "active": True,
                    "scopes": ["bus:send"],
                }
            ]
        )
        + "\n"
    )
    (tmp_path / "invites.json").write_text(
        json.dumps(
            [
                {
                    "id": "inv-dup",
                    "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                    "tenant_id": "sos",
                    "role": "member",
                    "scopes": ["bus:send", "tasks:*"],
                    "active": True,
                    "max_uses": 1,
                    "uses": 0,
                }
            ]
        )
        + "\n"
    )
    monkeypatch.setattr(sse.requests, "put", lambda *a, **k: None)

    resp = client.post(
        "/api/v1/onboarding/join-with-invite",
        json={
            "invite_code": code,
            "agent_name": "hadi-codex",
            "model": "codex",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "joined"
    assert body["agent"] == "hadi-codex-2"
    assert body["requested_agent"] == "hadi-codex"
    assert body["renamed_for_collision"] is True
    assert body["token"].startswith("sk-agent-sos-hadi-codex-2-")
    assert body["identity"]["agent"] == "hadi-codex-2"

    records = json.loads((tmp_path / "tokens.json").read_text())
    assert [record["agent"] for record in records] == ["hadi-codex", "hadi-codex-2"]
    assert records[1]["requested_agent"] == "hadi-codex"
    assert records[1]["renamed_for_collision"] is True


def test_join_with_invite_is_idempotent_for_same_install_id(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    code = "invite-sos-install"
    token = "sk-agent-sos-hadi-codex-2-existing"
    (tmp_path / "tokens.json").write_text(
        json.dumps(
            [
                {
                    "token": token,
                    "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                    "project": "sos",
                    "agent": "hadi-codex-2",
                    "requested_agent": "hadi-codex",
                    "renamed_for_collision": True,
                    "scope": "tenant-agent",
                    "role": "member",
                    "active": True,
                    "scopes": ["bus:send"],
                    "onboarding_install_id": "macbook-codex",
                }
            ]
        )
        + "\n"
    )
    (tmp_path / "invites.json").write_text(
        json.dumps(
            [
                {
                    "id": "inv-install",
                    "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                    "tenant_id": "sos",
                    "role": "member",
                    "scopes": ["bus:send", "tasks:*"],
                    "active": True,
                    "max_uses": 3,
                    "uses": 0,
                }
            ]
        )
        + "\n"
    )
    monkeypatch.setattr(sse.requests, "put", lambda *a, **k: None)

    resp = client.post(
        "/api/v1/onboarding/join-with-invite",
        json={
            "invite_code": code,
            "agent_name": "hadi-codex",
            "model": "codex",
            "install_id": "macbook-codex",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "already_joined"
    assert body["agent"] == "hadi-codex-2"
    assert body["token"] == token

    records = json.loads((tmp_path / "tokens.json").read_text())
    assert len(records) == 1
    invites = json.loads((tmp_path / "invites.json").read_text())
    assert invites[0]["uses"] == 0


def test_login_returns_scoped_context_for_invited_agent(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    raw = "sk-agent-acme-codex-abc"
    (tmp_path / "tokens.json").write_text(
        json.dumps(
            [
                {
                    "token": raw,
                    "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
                    "project": "acme",
                    "agent": "codex-acme",
                    "scope": "tenant-agent",
                    "role": "member",
                    "active": True,
                    "scopes": ["bus:send"],
                }
            ]
        )
        + "\n"
    )
    sse._local_token_cache.invalidate()

    resp = client.post("/api/v1/onboarding/login", json={"token": raw})

    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["tenant_id"] == "acme"
    assert body["agent"] == "codex-acme"
    assert body["scope"] == "tenant-agent"
    assert body["role"] == "member"
