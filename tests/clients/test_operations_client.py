"""v0.4.6 Step 1 — P1-07 close.

Before: sos.clients.operations.OperationsClient imported
sos.services.operations.runner in-process (R2 violation).

After: OperationsClient + AsyncOperationsClient are real HTTP clients
built on BaseHTTPClient / AsyncBaseHTTPClient, and the operations
service ships a FastAPI app wrapping run_operation + load_template.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "sos" / "clients"
OPERATIONS_CLIENT = CLIENTS_DIR / "operations.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for n in node.names:
                mods.add(f"{node.module}.{n.name}")
    return mods


def test_operations_client_does_not_import_service_runner():
    mods = _imported_modules(OPERATIONS_CLIENT)
    leaks = [m for m in mods if m.startswith("sos.services.operations")]
    assert leaks == [], f"clients/operations.py still reaches into service: {leaks}"


def test_operations_client_uses_base_http_client():
    src = OPERATIONS_CLIENT.read_text(encoding="utf-8")
    assert "from sos.clients.base import" in src
    assert "BaseHTTPClient" in src
    assert "AsyncBaseHTTPClient" in src


def test_sync_client_run_posts_to_run_endpoint():
    from sos.clients.operations import OperationsClient

    client = OperationsClient(base_url="http://fake-operations:6068", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"status": "ok", "cycle": 42}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.run("acme", "content-writer", dry_run=False)

    assert mock_req.call_args.args == ("POST", "/run")
    assert mock_req.call_args.kwargs["json"] == {
        "customer": "acme",
        "product": "content-writer",
        "dry_run": False,
    }
    assert out["status"] == "ok"


def test_sync_client_list_templates_reads_templates_key():
    from sos.clients.operations import OperationsClient

    client = OperationsClient(base_url="http://fake:6068", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"templates": ["a", "b", "c"]}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.list_templates()

    assert mock_req.call_args.args == ("GET", "/templates")
    assert out == ["a", "b", "c"]


def test_sync_client_get_template_returns_none_on_404():
    from sos.clients.base import SOSClientError
    from sos.clients.operations import OperationsClient

    client = OperationsClient(base_url="http://fake:6068", token="admin")
    err = SOSClientError(status_code=404, message="Not Found", body=None)
    with patch.object(client, "_request", side_effect=err):
        assert client.get_template("missing") is None


def test_token_resolves_from_env(monkeypatch):
    from sos.clients.operations import _resolve_token

    monkeypatch.delenv("SOS_OPERATIONS_TOKEN", raising=False)
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-tok")
    assert _resolve_token(None) == "sys-tok"
    assert _resolve_token("explicit") == "explicit"


@pytest.mark.asyncio
async def test_async_client_run_posts_to_run_endpoint():
    from sos.clients.operations import AsyncOperationsClient
    from unittest.mock import AsyncMock

    client = AsyncOperationsClient(base_url="http://fake:6068", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"status": "ok"}

    with patch.object(client, "_request", AsyncMock(return_value=fake_resp)) as mock_req:
        out = await client.run("acme", "content-writer")

    assert mock_req.call_args.args == ("POST", "/run")
    assert mock_req.call_args.kwargs["json"]["customer"] == "acme"
    assert out["status"] == "ok"
