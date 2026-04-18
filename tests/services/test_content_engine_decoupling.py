"""Tests for P0-03 content→engine decoupling.

Proves that content no longer reaches into engine internals:

1. **Static**: the content service source code contains no
   ``from sos.services.engine`` / ``import sos.services.engine`` string.
   This is a stronger invariant than ``sys.modules`` because importing
   ``sos.services.content.app`` in a test process that already loaded
   the engine (via fixtures, collection, or other tests) would
   falsely pass a sys.modules-based check.

2. **Behavioral**: running ``_run_generation_loop`` with a fake
   ``AsyncEngineClient`` + ``SwarmCouncil`` produces the correct
   ``ChatRequest`` (agent_id + model) and a council proposal.

Pattern mirrors ``tests/services/test_health_squad_decoupling.py``.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sos.contracts.engine import ChatRequest, ChatResponse
from sos.services.content.calendar import PostStatus, ScheduledPost
from sos.services.content.orchestrator import ContentOrchestrator


CONTENT_DIR = Path(__file__).resolve().parents[2] / "sos" / "services" / "content"


# ---------------------------------------------------------------------------
# Static: content no longer imports engine internals
# ---------------------------------------------------------------------------

def _collect_imports(source: str) -> list[str]:
    """Return fully-qualified module names referenced in ``import`` /
    ``from X import`` statements (top-level + nested)."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


def test_content_app_has_no_engine_service_import():
    """sos/services/content/app.py must not import sos.services.engine.*"""
    source = (CONTENT_DIR / "app.py").read_text()
    offenders = [
        name for name in _collect_imports(source)
        if name.startswith("sos.services.engine")
    ]
    assert offenders == [], (
        f"content/app.py still imports engine internals: {offenders}. "
        "P0-03 requires content talk to engine via clients/contracts only."
    )


def test_content_orchestrator_has_no_engine_service_import():
    """sos/services/content/orchestrator.py must not import sos.services.engine.*"""
    source = (CONTENT_DIR / "orchestrator.py").read_text()
    offenders = [
        name for name in _collect_imports(source)
        if name.startswith("sos.services.engine")
    ]
    assert offenders == [], (
        f"content/orchestrator.py still imports engine internals: {offenders}."
    )


def test_no_content_file_imports_engine_service():
    """Sweep: no file under sos/services/content/ imports sos.services.engine.*"""
    offenders: dict[str, list[str]] = {}
    for py in CONTENT_DIR.rglob("*.py"):
        source = py.read_text()
        bad = [
            name for name in _collect_imports(source)
            if name.startswith("sos.services.engine")
        ]
        if bad:
            offenders[str(py.relative_to(CONTENT_DIR))] = bad
    assert offenders == {}, f"content files still import engine internals: {offenders}"


# ---------------------------------------------------------------------------
# Behavioral: orchestrator uses the client's ChatRequest + council.propose
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_generation_loop_calls_client_with_chat_request():
    """_run_generation_loop must call engine.chat(ChatRequest(...)) and
    council.propose(...)."""

    # --- Arrange --------------------------------------------------------------
    fake_engine = AsyncMock()
    fake_engine.chat = AsyncMock(
        return_value=ChatResponse(
            content="# A fabulous draft\nBody body body.",
            agent_id="agent:River",
            model_used="gemini-3-flash-preview",
            conversation_id="test-conv",
        )
    )

    fake_council = AsyncMock()
    fake_council.propose = AsyncMock(return_value="prop_test_1")

    orchestrator = ContentOrchestrator(fake_engine, fake_council)

    # Isolate from on-disk calendar state the real ContentOrchestrator
    # eagerly constructs in __init__. Swap to an in-memory empty one.
    orchestrator.calendar.posts = []

    planted = ScheduledPost(
        id="post-001",
        title="The Organism Doesn't Weaken",
        pillar_id="sovereign-ai",
        format="blog_post",
        target_audience="developers",
        scheduled_date=datetime.now(timezone.utc) + timedelta(hours=1),
        status=PostStatus.PLANNED,
        keywords=["sos", "organism"],
        slug=None,
    )
    orchestrator.calendar.posts.append(planted)

    # --- Act ------------------------------------------------------------------
    await orchestrator._run_generation_loop(planted)

    # --- Assert engine.chat got a ChatRequest with the right shape -----------
    fake_engine.chat.assert_awaited_once()
    call_args = fake_engine.chat.await_args
    # The first positional arg should be a ChatRequest instance
    assert len(call_args.args) == 1, "engine.chat called with wrong arity"
    request = call_args.args[0]
    assert isinstance(request, ChatRequest), (
        f"engine.chat was called with {type(request)!r}, not ChatRequest"
    )
    assert request.agent_id == "agent:River"
    assert request.model == "gemini-3-flash-preview"
    assert request.memory_enabled is True
    assert planted.title in request.message

    # --- Assert council.propose was called with the right envelope -----------
    fake_council.propose.assert_awaited_once()
    propose_kwargs = fake_council.propose.await_args.kwargs
    assert propose_kwargs["agent_id"] == "agent:River"
    assert propose_kwargs["title"].startswith("CONTENT_WITNESS:")
    assert propose_kwargs["payload"]["post_id"] == planted.id
    assert propose_kwargs["payload"]["type"] == "content_approval"


@pytest.mark.asyncio
async def test_orchestrator_accepts_async_engine_client_signature():
    """Sanity: AsyncEngineClient and our fake have the same 1-arg chat
    signature. This prevents silent regressions if the contract changes."""
    from sos.clients.engine import AsyncEngineClient

    # The real client's chat signature takes a single ChatRequest arg.
    import inspect
    sig = inspect.signature(AsyncEngineClient.chat)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 1, (
        f"AsyncEngineClient.chat signature changed — expected 1 non-self "
        f"param, got {[p.name for p in params]}"
    )
    assert params[0].annotation is ChatRequest or params[0].annotation == "ChatRequest"
