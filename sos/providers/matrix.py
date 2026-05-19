"""Provider Matrix — thin config layer over existing SOS adapter backends.

Not a bespoke LLM router. Just a YAML-driven table of ProviderCards with
circuit breakers and tier-based selection. Actual execution goes through the
existing adapters in sos/adapters/.

v0.7.0 additions
----------------
- ``call_with_breaker`` async context manager — records adapter call
  outcomes on the breaker so state reflects real traffic, not just
  aggregate heuristics.
- ``probe_provider`` async helper — drives health_probe_url periodically
  to close the feedback loop even when no user traffic is flowing.
- ``select_with_fallback`` iterator — walks tier preferences in order so
  the brain doesn't have to reimplement fallback logic at every call site.
"""
from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("sos.providers.matrix")

# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent.parent / "contracts" / "schemas" / "provider_card_v1.json"


def load_schema() -> dict[str, Any]:
    """Return the parsed JSON Schema dict for ProviderCard v1."""
    return json.loads(_SCHEMA_PATH.read_text())

# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------

BackendLiteral = Literal[
    "claude-adapter",
    "claude-managed-agents",
    "openai-adapter",
    "openai-agents-sdk",
    "gemini-adapter",
    "langgraph",
    "local",
]

TierLiteral = Literal["primary", "fallback", "cheap", "premium", "local"]

CircuitStateLiteral = Literal["closed", "open", "half_open"]


class CircuitBreakerConfig(BaseModel):
    """Static config for a circuit breaker — thresholds only, no runtime state."""

    model_config = ConfigDict(strict=False)

    failure_threshold: int = Field(default=5, ge=1)
    recovery_window_seconds: int = Field(default=60, ge=1)
    half_open_max_requests: int = Field(default=1, ge=1)


class ProviderCard(BaseModel):
    """One row in the provider matrix."""

    model_config = ConfigDict(strict=False)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1)
    backend: BackendLiteral
    tier: TierLiteral
    model: str = Field(min_length=1)
    endpoint: Optional[str] = None
    health_probe_url: Optional[str] = None
    timeout_seconds: int = Field(default=60, ge=1)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    cost_per_call_estimate_micros: Optional[int] = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Runtime circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreaker:
    """Runtime circuit breaker for one ProviderCard.

    State machine:
      closed → open   : after `failure_threshold` consecutive failures
      open   → half_open : after `recovery_window_seconds` since last state change
      half_open → closed : on success
      half_open → open  : on failure (resets recovery window)
    """

    config: CircuitBreakerConfig
    failures: int = field(default=0)
    state: CircuitStateLiteral = field(default="closed")
    last_state_change: float = field(default_factory=time.monotonic)

    def record_success(self) -> None:
        if self.state in ("half_open", "open"):
            self.state = "closed"
            self.last_state_change = time.monotonic()
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open":
            # Back to open, reset recovery window
            self.state = "open"
            self.last_state_change = time.monotonic()
        elif self.state == "closed" and self.failures >= self.config.failure_threshold:
            self.state = "open"
            self.last_state_change = time.monotonic()

    def is_open(self) -> bool:
        """Return True if requests should be blocked.

        Side effect: transitions open → half_open when recovery window has passed.
        """
        if self.state == "closed":
            return False
        if self.state == "open":
            elapsed = time.monotonic() - self.last_state_change
            if elapsed >= self.config.recovery_window_seconds:
                self.state = "half_open"
                self.last_state_change = time.monotonic()
                return False  # allow one probe through
            return True
        # half_open: let requests through
        return False


# ---------------------------------------------------------------------------
# Matrix loader
# ---------------------------------------------------------------------------

_DEFAULT_YAML = Path(__file__).parent / "providers.yaml"


class ProviderMatrixError(Exception):
    """Raised when no suitable provider can be selected."""


def load_matrix(path: str | None = None) -> list[ProviderCard]:
    """Load ProviderCards from a YAML file.

    Defaults to sos/providers/providers.yaml. Pass an override path for tests.
    """
    import yaml  # optional dep; only needed at load time

    yaml_path = Path(path) if path else _DEFAULT_YAML
    raw = yaml.safe_load(yaml_path.read_text())
    providers_raw = raw.get("providers", raw) if isinstance(raw, dict) else raw
    return [ProviderCard.model_validate(entry) for entry in providers_raw]


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

# Module-level breaker registry: provider_id → CircuitBreaker
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(card: ProviderCard) -> CircuitBreaker:
    """Return (or create) the CircuitBreaker for a given ProviderCard."""
    if card.id not in _breakers:
        _breakers[card.id] = CircuitBreaker(config=card.circuit_breaker)
    return _breakers[card.id]


def select_provider(
    matrix: list[ProviderCard],
    tier: TierLiteral,
    healthy_only: bool = True,
) -> ProviderCard:
    """Pick the first ProviderCard matching `tier` with a non-open circuit breaker.

    Raises ProviderMatrixError if no matching card is available.
    """
    candidates = [c for c in matrix if c.tier == tier]
    if not candidates:
        raise ProviderMatrixError(f"No providers registered for tier={tier!r}")

    for card in candidates:
        if not healthy_only:
            return card
        breaker = get_breaker(card)
        if not breaker.is_open():
            return card

    raise ProviderMatrixError(
        f"All providers for tier={tier!r} have open circuit breakers"
    )


# ---------------------------------------------------------------------------
# v0.7.0 — hardening primitives
# ---------------------------------------------------------------------------


def select_with_fallback(
    matrix: list[ProviderCard],
    tiers: Sequence[TierLiteral],
    healthy_only: bool = True,
) -> Iterator[ProviderCard]:
    """Yield healthy cards in *tiers* order, skipping open breakers.

    Usage::

        for card in select_with_fallback(matrix, ["primary", "fallback", "cheap"]):
            try:
                async with call_with_breaker(card):
                    return await adapter.call(card, prompt)
            except Exception:
                continue  # next card
        raise ProviderMatrixError("all tiers exhausted")

    The iterator is lazy — ``get_breaker(card).is_open()`` is evaluated at
    iteration time, so a breaker that transitions open mid-sweep is respected.
    """
    for tier in tiers:
        for card in matrix:
            if card.tier != tier:
                continue
            if healthy_only and get_breaker(card).is_open():
                continue
            yield card


@contextlib.asynccontextmanager
async def call_with_breaker(card: ProviderCard):
    """Async context manager that records success/failure on *card*'s breaker.

    Raises :class:`ProviderMatrixError` up front if the breaker is open, so
    callers don't have to double-check. Any exception from the ``async with``
    body is recorded as a failure and re-raised.

    Example::

        async with call_with_breaker(card):
            return await some_adapter.call(card, prompt)
    """
    breaker = get_breaker(card)
    if breaker.is_open():
        raise ProviderMatrixError(
            f"circuit breaker for provider_id={card.id!r} is open"
        )
    try:
        yield card
    except Exception:
        breaker.record_failure()
        raise
    else:
        breaker.record_success()


async def probe_provider(card: ProviderCard, timeout: float | None = None) -> bool:
    """Probe *card*'s health_probe_url, record the outcome on its breaker.

    Returns ``True`` on 2xx, ``False`` on timeout / non-2xx / no URL. A card
    without a ``health_probe_url`` is treated as "not probeable" — the
    function returns ``False`` but does *not* record a failure (breaker state
    is untouched), so static config doesn't trigger false opens.

    ``timeout`` defaults to ``card.timeout_seconds``. Uses ``httpx.AsyncClient``
    so it respects trust_env + any OTEL instrumentation already configured.
    """
    if not card.health_probe_url:
        return False

    try:
        import httpx  # imported lazily so the matrix module stays importable
    except ImportError:  # pragma: no cover — httpx is a hard dep
        logger.warning("httpx not available; cannot probe %s", card.id)
        return False

    effective_timeout = timeout if timeout is not None else float(card.timeout_seconds)
    breaker = get_breaker(card)
    try:
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            resp = await client.get(card.health_probe_url)
        if 200 <= resp.status_code < 300:
            breaker.record_success()
            return True
        breaker.record_failure()
        logger.info(
            "probe failed for %s: HTTP %d", card.id, resp.status_code
        )
        return False
    except Exception as exc:
        breaker.record_failure()
        logger.info("probe failed for %s: %s", card.id, exc)
        return False


def reset_breakers() -> None:
    """Clear the module-level breaker registry. Test-only helper."""
    _breakers.clear()
