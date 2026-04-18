"""Tests for sos/providers/matrix.py — ProviderCard, CircuitBreaker, load_matrix, select_provider."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from sos.providers.matrix import (
    CircuitBreaker,
    CircuitBreakerConfig,
    ProviderCard,
    ProviderMatrixError,
    _breakers,
    load_matrix,
    select_provider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_YAML_DIR = Path(__file__).parent / "fixtures"

MINIMAL_CARD_DATA = {
    "id": "test-provider",
    "name": "Test Provider",
    "backend": "claude-adapter",
    "tier": "primary",
    "model": "claude-sonnet-4-6",
}


def make_card(**kwargs) -> ProviderCard:
    data = {**MINIMAL_CARD_DATA, **kwargs}
    return ProviderCard.model_validate(data)


def make_breaker(failure_threshold: int = 3, recovery_window_seconds: int = 60) -> CircuitBreaker:
    cfg = CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        recovery_window_seconds=recovery_window_seconds,
    )
    return CircuitBreaker(config=cfg)


# ---------------------------------------------------------------------------
# ProviderCard construction
# ---------------------------------------------------------------------------


def test_provider_card_happy_path():
    card = make_card()
    assert card.id == "test-provider"
    assert card.backend == "claude-adapter"
    assert card.tier == "primary"
    assert card.timeout_seconds == 60  # default


def test_provider_card_all_backends():
    backends = [
        "claude-adapter",
        "claude-managed-agents",
        "openai-adapter",
        "openai-agents-sdk",
        "gemini-adapter",
        "langgraph",
        "local",
    ]
    for b in backends:
        card = make_card(id=f"prov-{b.replace('-', '')}", backend=b)
        assert card.backend == b


def test_provider_card_bad_id_uppercase():
    with pytest.raises(ValidationError):
        make_card(id="Bad-ID")


def test_provider_card_bad_id_starts_with_digit():
    with pytest.raises(ValidationError):
        make_card(id="1bad")


def test_provider_card_bad_backend():
    with pytest.raises(ValidationError):
        make_card(backend="unknown-backend")


def test_provider_card_bad_tier():
    with pytest.raises(ValidationError):
        make_card(tier="ultra")


def test_provider_card_optional_fields():
    card = make_card(
        endpoint="https://api.example.com",
        health_probe_url="https://api.example.com/health",
        cost_per_call_estimate_micros=5000,
    )
    assert card.endpoint == "https://api.example.com"
    assert card.cost_per_call_estimate_micros == 5000


# ---------------------------------------------------------------------------
# Matrix load from YAML
# ---------------------------------------------------------------------------


def test_load_matrix_default():
    """Load the bundled providers.yaml — expect 6 entries."""
    matrix = load_matrix()
    assert len(matrix) == 6
    ids = [c.id for c in matrix]
    assert "claude-sonnet-46" in ids
    assert "gemini-25-flash" in ids


def test_load_matrix_custom_yaml(tmp_path):
    yaml_content = """
providers:
  - id: my-provider
    name: My Provider
    backend: local
    tier: local
    model: local-llm
"""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text(yaml_content)
    matrix = load_matrix(str(yaml_file))
    assert len(matrix) == 1
    assert matrix[0].id == "my-provider"


# ---------------------------------------------------------------------------
# Circuit breaker state transitions
# ---------------------------------------------------------------------------


def test_circuit_breaker_starts_closed():
    cb = make_breaker()
    assert cb.state == "closed"
    assert not cb.is_open()


def test_circuit_breaker_opens_after_threshold():
    cb = make_breaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"
    cb.record_failure()
    assert cb.state == "open"
    assert cb.is_open()


def test_circuit_breaker_open_to_half_open_after_window():
    cb = make_breaker(failure_threshold=1, recovery_window_seconds=1)
    cb.record_failure()
    assert cb.state == "open"
    # Fake that enough time has passed
    cb.last_state_change = time.monotonic() - 2  # 2s ago > 1s window
    assert not cb.is_open()  # should transition to half_open
    assert cb.state == "half_open"


def test_circuit_breaker_half_open_to_closed_on_success():
    cb = make_breaker(failure_threshold=1, recovery_window_seconds=1)
    cb.record_failure()
    cb.last_state_change = time.monotonic() - 2
    cb.is_open()  # triggers → half_open
    cb.record_success()
    assert cb.state == "closed"
    assert cb.failures == 0


def test_circuit_breaker_half_open_to_open_on_failure():
    cb = make_breaker(failure_threshold=1, recovery_window_seconds=1)
    cb.record_failure()
    cb.last_state_change = time.monotonic() - 2
    cb.is_open()  # → half_open
    cb.record_failure()
    assert cb.state == "open"


def test_circuit_breaker_success_resets_failure_count():
    cb = make_breaker(failure_threshold=5)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.failures == 0
    assert cb.state == "closed"


# ---------------------------------------------------------------------------
# select_provider
# ---------------------------------------------------------------------------


def test_select_provider_picks_first_matching_tier():
    _breakers.clear()
    matrix = [
        make_card(id="primary-a", tier="primary"),
        make_card(id="primary-b", tier="primary", model="claude-opus-4-7"),
    ]
    card = select_provider(matrix, "primary")
    assert card.id == "primary-a"


def test_select_provider_skips_open_breaker():
    _breakers.clear()
    card_a = make_card(id="primary-a", tier="primary")
    card_b = make_card(id="primary-b", tier="primary", model="claude-opus-4-7")
    matrix = [card_a, card_b]

    # Force card_a's breaker open
    from sos.providers.matrix import get_breaker

    breaker_a = get_breaker(card_a)
    breaker_a.state = "open"
    breaker_a.last_state_change = time.monotonic()  # just tripped, not recovered

    card = select_provider(matrix, "primary")
    assert card.id == "primary-b"


def test_select_provider_raises_when_none_healthy():
    _breakers.clear()
    card_a = make_card(id="only-one", tier="fallback")
    from sos.providers.matrix import get_breaker

    b = get_breaker(card_a)
    b.state = "open"
    b.last_state_change = time.monotonic()

    with pytest.raises(ProviderMatrixError):
        select_provider([card_a], "fallback")


def test_select_provider_raises_for_unknown_tier():
    _breakers.clear()
    matrix = [make_card(id="p1", tier="primary")]
    with pytest.raises(ProviderMatrixError):
        select_provider(matrix, "cheap")  # type: ignore[arg-type]


def test_select_provider_healthy_only_false_returns_first():
    _breakers.clear()
    card_a = make_card(id="broken-a", tier="cheap")
    from sos.providers.matrix import get_breaker

    b = get_breaker(card_a)
    b.state = "open"
    b.last_state_change = time.monotonic()

    card = select_provider([card_a], "cheap", healthy_only=False)
    assert card.id == "broken-a"
