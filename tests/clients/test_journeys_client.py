"""v0.4.6 Steps 4+5 — P1-05 close (journeys half).

Before: sos.agents.join imported sos.services.journeys.tracker.JourneyTracker
in-process (R2 violation).

After: journeys ships a FastAPI app at sos/services/journeys/app.py, and
sos.clients.journeys.{JourneysClient,AsyncJourneysClient} proxy the same
surface over HTTP via BaseHTTPClient / AsyncBaseHTTPClient.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "sos" / "clients"
JOURNEYS_CLIENT = CLIENTS_DIR / "journeys.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for n in node.names:
                mods.add(f"{node.module}.{n.name}")
    return mods


def test_journeys_client_does_not_import_service():
    mods = _imported_modules(JOURNEYS_CLIENT)
    leaks = [m for m in mods if m.startswith("sos.services.journeys")]
    assert leaks == [], f"clients/journeys.py still reaches into service: {leaks}"


def test_journeys_client_uses_base_http_client():
    src = JOURNEYS_CLIENT.read_text(encoding="utf-8")
    assert "from sos.clients.base import" in src
    assert "BaseHTTPClient" in src
    assert "AsyncBaseHTTPClient" in src


def test_sync_recommend_hits_recommend_endpoint():
    from sos.clients.journeys import JourneysClient

    client = JourneysClient(base_url="http://fake:6070", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"agent": "acme-bot", "path": "builder"}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.recommend("acme-bot")

    assert mock_req.call_args.args == ("GET", "/recommend/acme-bot")
    assert out == "builder"


def test_sync_start_posts_to_start():
    from sos.clients.journeys import JourneysClient

    client = JourneysClient(base_url="http://fake:6070", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"display": "Builder Path"}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.start("acme-bot", "builder")

    assert mock_req.call_args.args == ("POST", "/start")
    assert mock_req.call_args.kwargs["json"] == {"agent": "acme-bot", "path": "builder"}
    assert out["display"] == "Builder Path"


def test_sync_leaderboard_optional_filter():
    from sos.clients.journeys import JourneysClient

    client = JourneysClient(base_url="http://fake:6070", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"leaders": [{"agent": "a"}, {"agent": "b"}]}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.leaderboard()
    assert mock_req.call_args.args == ("GET", "/leaderboard")
    assert len(out) == 2

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        client.leaderboard(path="builder")
    assert mock_req.call_args.args == ("GET", "/leaderboard?path=builder")


def test_token_resolves_from_env(monkeypatch):
    from sos.clients.journeys import _resolve_token

    monkeypatch.delenv("SOS_JOURNEYS_TOKEN", raising=False)
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-tok")
    assert _resolve_token(None) == "sys-tok"
    assert _resolve_token("explicit") == "explicit"


@pytest.mark.asyncio
async def test_async_recommend_hits_recommend_endpoint():
    from sos.clients.journeys import AsyncJourneysClient

    client = AsyncJourneysClient(base_url="http://fake:6070", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"agent": "acme-bot", "path": "wordsmith"}

    with patch.object(client, "_request", AsyncMock(return_value=fake_resp)) as mock_req:
        path = await client.recommend("acme-bot")

    assert mock_req.call_args.args == ("GET", "/recommend/acme-bot")
    assert path == "wordsmith"
