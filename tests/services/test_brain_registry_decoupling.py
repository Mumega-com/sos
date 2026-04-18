"""Tests for P0-09 brain→registry decoupling.

Proves that brain no longer reaches into registry internals:

1. **Static**: no file under sos/services/brain/ contains a
   ``sos.services.registry`` import string.

2. **Behavioral**: the dispatch path calls
   ``AsyncRegistryClient.list_agents`` and handles both empty and populated
   candidate lists.

Pattern mirrors ``tests/services/test_analytics_integrations_decoupling.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

try:
    import fakeredis.aioredis as fake_aioredis  # type: ignore[import-untyped]
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False


BRAIN_DIR = Path(__file__).resolve().parents[2] / "sos" / "services" / "brain"

skipif_no_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS, reason="fakeredis not installed"
)


# ---------------------------------------------------------------------------
# Static: brain no longer imports registry
# ---------------------------------------------------------------------------


def _collect_imports(source: str) -> list[str]:
    """Return fully-qualified module names referenced in imports."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


def test_no_brain_file_imports_registry_service() -> None:
    """AST sweep: no file under sos/services/brain/ imports sos.services.registry.*"""
    offenders: dict[str, list[str]] = {}
    for py in BRAIN_DIR.rglob("*.py"):
        source = py.read_text()
        bad = [
            name for name in _collect_imports(source)
            if name == "sos.services.registry"
            or name.startswith("sos.services.registry.")
        ]
        if bad:
            offenders[str(py.relative_to(BRAIN_DIR))] = bad
    assert offenders == {}, (
        f"brain files still import registry internals: {offenders}. "
        "P0-09 requires brain reach registry via clients/contracts only."
    )


def test_brain_service_exposes_registry_client() -> None:
    """Behavioural import-shape: brain.service must surface AsyncRegistryClient."""
    from sos.services.brain import service as brain_service_module

    assert hasattr(brain_service_module, "AsyncRegistryClient")
    assert hasattr(brain_service_module, "_registry_client")


# ---------------------------------------------------------------------------
# Behavioral: BrainService._try_dispatch_next uses the HTTP client
# ---------------------------------------------------------------------------


def _build_agent(name: str, caps: list[str]) -> object:
    from sos.kernel.identity import AgentIdentity

    ident = AgentIdentity(name=name, model="test-model")
    ident.capabilities = list(caps)
    return ident


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_dispatch_uses_registry_client_and_handles_empty_list() -> None:
    """When the client returns [], no agent is selected; task re-queues."""
    from sos.services.brain.service import BrainService

    redis_client = fake_aioredis.FakeRedis(decode_responses=True)
    svc = BrainService(
        redis_url="redis://localhost:6379",
        stream_patterns=["sos:stream:global:squad:*"],
        redis_client=redis_client,
    )
    svc._redis = redis_client

    svc.state.enqueue("task-1", score=10.0)
    svc.state.task_skills["task-1"] = ["python"]

    with patch(
        "sos.services.brain.service._registry_client.list_agents",
        new=AsyncMock(return_value=[]),
    ) as mock_list:
        await svc._try_dispatch_next()

    mock_list.assert_awaited_once()
    # Empty candidates → no select; task stays on queue.
    assert svc.state.queue_size() == 1
    assert "task-1" not in svc.state.tasks_in_flight


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_dispatch_uses_registry_client_and_routes_on_match() -> None:
    """Populated candidate list: matching agent is selected + routed."""
    from sos.services.brain.service import BrainService

    redis_client = fake_aioredis.FakeRedis(decode_responses=True)
    svc = BrainService(
        redis_url="redis://localhost:6379",
        stream_patterns=["sos:stream:global:squad:*"],
        redis_client=redis_client,
    )
    svc._redis = redis_client

    svc.state.enqueue("task-42", score=50.0)
    svc.state.task_skills["task-42"] = ["python"]

    agent = _build_agent("alpha", ["python", "sql"])

    with patch(
        "sos.services.brain.service._registry_client.list_agents",
        new=AsyncMock(return_value=[agent]),
    ) as mock_list:
        await svc._try_dispatch_next()

    mock_list.assert_awaited_once()
    # On match: task leaves the queue and lands in_flight.
    assert svc.state.queue_size() == 0
    assert "task-42" in svc.state.tasks_in_flight
    # A routing decision is recorded.
    assert any(
        rd.task_id == "task-42" and rd.agent_name == "alpha"
        for rd in svc.state.recent_routing_decisions
    )


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_dispatch_client_exception_requeues_task() -> None:
    """Client errors must not lose the task — it re-queues."""
    from sos.services.brain.service import BrainService

    redis_client = fake_aioredis.FakeRedis(decode_responses=True)
    svc = BrainService(
        redis_url="redis://localhost:6379",
        stream_patterns=["sos:stream:global:squad:*"],
        redis_client=redis_client,
    )
    svc._redis = redis_client

    svc.state.enqueue("task-err", score=7.0)
    svc.state.task_skills["task-err"] = ["python"]

    with patch(
        "sos.services.brain.service._registry_client.list_agents",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await svc._try_dispatch_next()

    assert svc.state.queue_size() == 1
    assert "task-err" not in svc.state.tasks_in_flight
