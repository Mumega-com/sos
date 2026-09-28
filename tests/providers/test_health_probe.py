"""Tests for sos.providers.health_probe adapter resolution.

The bundled providers.yaml routes gemini-25-flash through the Vertex (ADC)
backend. These tests pin that the probe resolves that backend to
VertexGeminiAdapter and uses its health_check(), instead of falling through
to the optimistic (True, 0) default.

google-genai is an optional extra, so the adapter module is replaced with a
stub; the tests exercise the resolution path, not Vertex itself.
"""
from __future__ import annotations

import sys
import types

import pytest

from sos.providers import health_probe
from sos.providers.matrix import ProviderCard, load_matrix


class _StubVertexGeminiAdapter:
    healthy = False

    async def health_check(self) -> bool:
        return self.healthy


@pytest.fixture
def stub_vertex_adapter(monkeypatch: pytest.MonkeyPatch) -> type[_StubVertexGeminiAdapter]:
    module = types.ModuleType("sos.adapters.vertex_gemini_adapter")
    module.VertexGeminiAdapter = _StubVertexGeminiAdapter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sos.adapters.vertex_gemini_adapter", module)
    return _StubVertexGeminiAdapter


def _vertex_card() -> ProviderCard:
    return ProviderCard.model_validate(
        {
            "id": "vertex-test",
            "name": "Vertex Test",
            "backend": "vertex-gemini-adapter",
            "tier": "cheap",
            "model": "gemini-2.5-flash",
        }
    )


def test_resolve_adapter_maps_vertex_backend(stub_vertex_adapter) -> None:
    adapter = health_probe._resolve_adapter(_vertex_card())
    assert isinstance(adapter, stub_vertex_adapter)


async def test_probe_vertex_card_uses_adapter_health_check(stub_vertex_adapter) -> None:
    healthy, _latency_ms = await health_probe.probe(_vertex_card())
    assert healthy is False


async def test_probe_all_default_matrix_probes_vertex_card(
    stub_vertex_adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the other backends on the no-adapter path so nothing touches the network.
    real_resolve = health_probe._resolve_adapter
    monkeypatch.setattr(
        health_probe,
        "_resolve_adapter",
        lambda card: real_resolve(card) if card.backend == "vertex-gemini-adapter" else None,
    )

    matrix = load_matrix()
    results = await health_probe.probe_all(matrix)

    assert set(results) == {card.id for card in matrix}
    assert results["gemini-25-flash"][0] is False


def test_resolve_adapter_returns_none_when_vertex_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A None entry makes the import raise ImportError, as when google-genai is absent.
    monkeypatch.setitem(sys.modules, "sos.adapters.vertex_gemini_adapter", None)
    assert health_probe._resolve_adapter(_vertex_card()) is None
