"""Task #144, Step 1.1.c — AsyncRegistryClient unit tests.

Covers the HTTP client introduced by P0-09 (Brain → Registry decoupling):

1. ``list_agents()`` success → returns a list of :class:`AgentIdentity`.
2. ``get_agent("name")`` success → returns a single :class:`AgentIdentity`.
3. ``get_agent("missing")`` → 404 → returns ``None`` (per impl, caught in
   ``except SOSClientError`` branch).
4. Transport-level failures (``httpx.ConnectError``) on both methods — the
   client does NOT catch these; the exception propagates. These tests pin
   the current behaviour.

The HTTP layer is mocked via :class:`httpx.MockTransport`. Because
:class:`AsyncBaseHTTPClient` constructs a fresh :class:`httpx.AsyncClient`
inside ``_request``, we patch the ``AsyncClient`` symbol in
``sos.clients.base`` with a factory that injects our transport.
"""

from __future__ import annotations

import json
from typing import Callable
from unittest.mock import patch

import httpx
import pytest

from sos.clients.registry import AsyncRegistryClient
from sos.kernel.identity import AgentIdentity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_async_client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[..., httpx.AsyncClient]:
    """Return a drop-in replacement for ``httpx.AsyncClient`` that injects a
    MockTransport wrapping ``handler``. Preserves kwargs the real client is
    constructed with (``base_url``, ``timeout``). Uses the captured real
    class to avoid recursing into the patch."""
    transport = httpx.MockTransport(handler)

    def _factory(*args, **kwargs) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    return _factory


def _serialized_agent(name: str, capabilities: list[str] | None = None) -> dict:
    """Produce a dict shaped like the registry service's JSON response."""
    ident = AgentIdentity(name=name, model="gemini")
    ident.capabilities = list(capabilities or [])
    return ident.to_dict()


# ---------------------------------------------------------------------------
# list_agents — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents_returns_agent_identities() -> None:
    body = {
        "agents": [
            _serialized_agent("alpha", ["python", "sql"]),
            _serialized_agent("beta", ["typescript"]),
        ],
        "count": 2,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/agents"
        assert request.headers.get("authorization") == "Bearer test-token"
        return httpx.Response(200, json=body)

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="test-token")
        agents = await client.list_agents()

    assert len(agents) == 2
    assert all(isinstance(a, AgentIdentity) for a in agents)
    names = {a.name for a in agents}
    assert names == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# get_agent — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_returns_agent_identity_on_200() -> None:
    body = _serialized_agent("gamma", ["rust"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/agents/gamma"
        return httpx.Response(200, json=body)

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="test-token")
        agent = await client.get_agent("gamma")

    assert isinstance(agent, AgentIdentity)
    assert agent.name == "gamma"
    assert "rust" in agent.capabilities


# ---------------------------------------------------------------------------
# get_agent — 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_returns_none_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=json.dumps({"detail": "not found"}).encode())

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="test-token")
        result = await client.get_agent("missing-agent")

    assert result is None


# ---------------------------------------------------------------------------
# Network failure — httpx.ConnectError propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents_propagates_connect_error() -> None:
    """The client has no timeout/connect-error fallback — the exception bubbles.

    Flagged for Loom: callers (e.g. BrainService._try_dispatch_next) must
    wrap this in their own try/except, which brain does. Other consumers may
    not — consider adding an in-client graceful fallback if the policy says so.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="test-token")
        with pytest.raises(httpx.ConnectError):
            await client.list_agents()


@pytest.mark.asyncio
async def test_get_agent_propagates_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="test-token")
        with pytest.raises(httpx.ConnectError):
            await client.get_agent("any-name")


# ---------------------------------------------------------------------------
# list_agents — non-404 HTTP error raises SOSClientError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents_raises_on_500() -> None:
    """Non-404 HTTP errors become SOSClientError (no swallowing)."""
    from sos.clients.base import SOSClientError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"kaboom")

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="test-token")
        with pytest.raises(SOSClientError) as exc_info:
            await client.list_agents()

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# enroll_mesh — happy path (minimal payload)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_mesh_posts_expected_body_and_returns_response() -> None:
    """enroll_mesh POSTs required fields; None-valued optional fields are omitted."""
    response_body = {
        "enrolled": True,
        "name": "myagent",
        "project": None,
        "stale_after": 300,
        "expires_in": 900,
    }

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/mesh/enroll"
        assert request.headers.get("authorization") == "Bearer mesh-token"
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=response_body)

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="mesh-token")
        result = await client.enroll_mesh(
            agent_id="agent:myagent",
            name="myagent",
            role="executor",
            skills=["code", "deploy"],
            squads=[],
        )

    assert result["enrolled"] is True
    assert result["stale_after"] == 300
    body = captured["body"]
    assert body["agent_id"] == "agent:myagent"
    assert body["name"] == "myagent"
    assert body["role"] == "executor"
    assert body["skills"] == ["code", "deploy"]
    assert body["squads"] == []
    # Optional fields not passed → must not appear in body
    assert "heartbeat_url" not in body
    assert "project" not in body


# ---------------------------------------------------------------------------
# enroll_mesh — optional fields included when set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_mesh_includes_optional_fields_when_set() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"enrolled": True, "name": "myagent", "stale_after": 300, "expires_in": 900},
        )

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="mesh-token")
        await client.enroll_mesh(
            agent_id="agent:myagent",
            name="myagent",
            role="builder",
            heartbeat_url="http://agent.local/health",
            project="mumega",
        )

    body = captured["body"]
    assert body["heartbeat_url"] == "http://agent.local/health"
    assert body["project"] == "mumega"


# ---------------------------------------------------------------------------
# enroll_mesh — non-2xx raises SOSClientError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_mesh_raises_on_non_2xx() -> None:
    from sos.clients.base import SOSClientError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, content=b'{"detail": "invalid agent_id"}')

    with patch(
        "sos.clients.base.httpx.AsyncClient",
        _mock_async_client_factory(handler),
    ):
        client = AsyncRegistryClient(base_url="http://fake-registry:6067", token="mesh-token")
        with pytest.raises(SOSClientError) as exc_info:
            await client.enroll_mesh(
                agent_id="bad-id",
                name="myagent",
                role="executor",
            )

    assert exc_info.value.status_code == 422
