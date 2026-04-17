"""Provider Matrix — thin config layer over existing SOS adapter backends.

Not a bespoke LLM router. Just a YAML-driven table of ProviderCards with
circuit breakers and tier-based selection. Actual execution goes through the
existing adapters in sos/adapters/.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
