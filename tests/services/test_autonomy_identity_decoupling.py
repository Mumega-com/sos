"""Tests for P0-07 autonomy→identity decoupling.

Proves that autonomy no longer reaches into identity internals:

1. **Static**: no file under sos/services/autonomy/ contains a
   ``sos.services.identity`` import string.

2. **Behavioral**: the ``_generate_avatar`` + ``_post_to_social`` paths
   call ``AsyncIdentityClient.generate_avatar`` / ``.on_alpha_drift`` with
   the expected UV16D + args.

Pattern mirrors ``tests/services/test_analytics_integrations_decoupling.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sos.contracts.identity import UV16D


AUTONOMY_DIR = Path(__file__).resolve().parents[2] / "sos" / "services" / "autonomy"


# ---------------------------------------------------------------------------
# Static: autonomy no longer imports identity internals
# ---------------------------------------------------------------------------


def _collect_imports(source: str) -> list[str]:
    """Return fully-qualified module names referenced in import statements."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


def test_no_autonomy_file_imports_identity_service():
    """Sweep: no file under sos/services/autonomy/ imports sos.services.identity.*"""
    offenders: dict[str, list[str]] = {}
    for py in AUTONOMY_DIR.rglob("*.py"):
        source = py.read_text()
        bad = [
            name
            for name in _collect_imports(source)
            if name.startswith("sos.services.identity")
        ]
        if bad:
            offenders[str(py.relative_to(AUTONOMY_DIR))] = bad
    assert offenders == {}, (
        f"autonomy files still import identity internals: {offenders}. "
        "P0-07 requires autonomy reach identity via clients/contracts only."
    )


def test_no_autonomy_source_contains_identity_avatar_string():
    """Source-string check: catches lazy/local imports the AST walk might miss."""
    offenders: list[str] = []
    for py in AUTONOMY_DIR.rglob("*.py"):
        source = py.read_text()
        if "sos.services.identity" in source:
            offenders.append(str(py.relative_to(AUTONOMY_DIR)))
    assert offenders == [], (
        f"autonomy source still references sos.services.identity: {offenders}"
    )


# ---------------------------------------------------------------------------
# Behavioral: AutonomyService uses AsyncIdentityClient correctly
# ---------------------------------------------------------------------------


def _make_service(tmp_path, *, enable_social: bool = False):
    """Construct an AutonomyService with a mocked identity client.

    We patch ``AsyncIdentityClient`` on the service module BEFORE the
    service constructs its client, so the instance stored on the service
    is already an AsyncMock — no real HTTP is ever attempted.
    """
    from sos.services.autonomy import service as autonomy_service

    fake_client = AsyncMock()
    fake_client.generate_avatar = AsyncMock(
        return_value={"success": True, "path": str(tmp_path / "a.png")}
    )
    fake_client.on_alpha_drift = AsyncMock(
        return_value={"triggered": True, "alpha": 0.0}
    )

    cfg = autonomy_service.AutonomyConfig(
        agent_id="agent:River",
        enable_dreams=False,  # we supply dreams directly below
        enable_avatar=True,
        enable_social=enable_social,
    )
    svc = autonomy_service.AutonomyService(agent_id="agent:River", config=cfg)
    # Replace the real AsyncIdentityClient with our mock.
    svc._identity_client = fake_client
    return svc, fake_client


def _make_dream(relevance: float = 0.9, is_breakthrough: bool = True):
    """Build a minimal Dream-shaped object for the avatar path."""
    return SimpleNamespace(
        relevance_score=relevance,
        dream_type="pattern_synthesis",
        is_breakthrough=is_breakthrough,
        insights="insight text",
        content="dream content " * 10,
        topics=["a", "b"],
    )


@pytest.mark.asyncio
async def test_generate_avatar_calls_identity_client(tmp_path):
    """_generate_avatar must call AsyncIdentityClient.generate_avatar with
    a UV16D and the expected event metadata."""
    svc, fake_client = _make_service(tmp_path)
    dream = _make_dream(relevance=0.9, is_breakthrough=True)

    await svc._generate_avatar(dream, alpha=0.0005)

    fake_client.generate_avatar.assert_awaited_once()
    kwargs = fake_client.generate_avatar.await_args.kwargs
    assert kwargs["agent_id"] == "River"
    assert kwargs["alpha_drift"] == 0.0005
    assert kwargs["event_type"] == "dream_synthesis"
    assert isinstance(kwargs["uv"], UV16D)
    # dream.relevance_score = 0.9 → p = 0.5 + 0.9*0.3 = 0.77
    assert kwargs["uv"].p == pytest.approx(0.5 + 0.9 * 0.3)
    # "pattern" in "pattern_synthesis" → mu = 0.7
    assert kwargs["uv"].mu == pytest.approx(0.7)
    # breakthrough → phi = 0.8
    assert kwargs["uv"].phi == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_post_to_social_calls_identity_client(tmp_path):
    """_post_to_social must call AsyncIdentityClient.on_alpha_drift with
    the dream's relevance as the UV phi component."""
    svc, fake_client = _make_service(tmp_path, enable_social=True)
    dream = _make_dream(relevance=0.65, is_breakthrough=False)

    await svc._post_to_social(dream)

    fake_client.on_alpha_drift.assert_awaited_once()
    kwargs = fake_client.on_alpha_drift.await_args.kwargs
    assert kwargs["agent_id"] == "River"
    assert kwargs["alpha_value"] == 0.0
    assert kwargs["insight"] == "insight text"
    assert kwargs["platforms"] == ["twitter"]
    assert isinstance(kwargs["uv"], UV16D)
    assert kwargs["uv"].phi == pytest.approx(0.65)


def test_identity_client_import_shape():
    """AutonomyService module must import AsyncIdentityClient + UV16D from
    the correct contracts/clients locations, and must NOT import avatar."""
    from sos.services.autonomy import service as autonomy_service

    assert hasattr(autonomy_service, "AsyncIdentityClient")
    assert hasattr(autonomy_service, "UV16D")
    # The module must NOT have surfaced any identity internals:
    assert not hasattr(autonomy_service, "AvatarGenerator")
    assert not hasattr(autonomy_service, "SocialAutomation")
