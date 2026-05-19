"""Pricing primitives for SOS LLM adapters.

SOS exposes two pricing shapes in `PricingEntry`:

  1. **Per-token pricing** — `input_per_mtok` + `output_per_mtok` (cents per 1M tokens).
     Fits chat / text models (Gemini/Claude/GPT).
  2. **Flat-per-call pricing** — `flat_cents_per_call`.
     Fits image-generation models (Imagen 4, Gemini 2.5 Flash Image, DALL-E 3).

A `PricingEntry` may define either shape. Use `entry.estimate_cents(...)` to
get the estimated cost — it routes based on which fields are populated.

**Boundary note (SEC: SOS vs Mumega):**
The entries shipped with SOS serve as the default catalog so operators on a
Raspberry Pi / private cloud can run without extra config. Mumega (and any
other commercial operator) is expected to override pricing at deploy via the
`SOS_PRICING_CONFIG` env var pointing at a YAML/JSON file. USD-denominated
invoicing, rate negotiation, and provider-specific volume tiers belong in that
operator-owned config, not in the SOS catalog.

Source-linked entries: every non-zero entry carries a `source` comment naming
where the rate was verified and on what date, so audits are cheap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import math


@dataclass
class PricingEntry:
    """Represents the billing model for one provider model id.

    Exactly one pricing shape should be populated per entry:
      - Per-token: set both `input_per_mtok` and `output_per_mtok` (cents / 1M tokens).
      - Flat-per-call: set `flat_cents_per_call` (integer cents per call).

    Both shapes zero = free-tier entry (e.g. current Gemma models).
    """
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    flat_cents_per_call: float = 0.0
    source: str = ""  # where the rate was verified + date, e.g. "ai.google.dev pricing, 2026-04-17"

    @property
    def is_flat(self) -> bool:
        return self.flat_cents_per_call > 0 and self.input_per_mtok == 0 and self.output_per_mtok == 0

    def estimate_cents(self, input_tokens: int = 0, output_tokens: int = 0, image_count: int = 0) -> int:
        """Estimate cost in integer cents for the given usage.

        Rounds up (ceil) any positive fractional result to the nearest cent —
        providers bill in fractions of a cent but the SOS ledger rounds up.
        """
        if self.is_flat:
            total = self.flat_cents_per_call * max(image_count, 1 if image_count == 0 else image_count)
            return int(math.ceil(total)) if total > 0 else 0

        cost = (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1_000_000
        if cost <= 0:
            return 0
        return max(1, int(math.ceil(cost)))

    def estimate_micros(self, input_tokens: int = 0, output_tokens: int = 0, image_count: int = 0) -> int:
        """Estimate cost in integer micros (1e-6 units) — finer grain than cents.

        Micros preserve precision for cheap calls (e.g. a single Gemini Flash Lite
        prompt costs ~4 micros; rounding to cents loses the signal). Used by the
        economy ledger's `cost_micros` field and by `UsageInfo.cost_micros`.
        """
        if self.is_flat:
            total = self.flat_cents_per_call * max(image_count, 1 if image_count == 0 else image_count)
            return int(math.ceil(total * 10_000))

        # cents per 1M tokens → micros per token: rate_cents_per_mtok * 10_000 / 1_000_000 = rate_cents_per_mtok / 100
        micros = (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) * 10_000 / 1_000_000
        return int(math.ceil(micros)) if micros > 0 else 0


PricingTable = dict[str, PricingEntry]


def ensure_entry(table: PricingTable, model: str, default: Optional[PricingEntry] = None) -> PricingEntry:
    """Get a `PricingEntry` for `model`, falling back to `default` or an empty entry.

    Unknown models no longer receive a punitive Pro-tier default cost (the bug
    reported in trop issue #97). Callers that want a conservative fallback
    should pass one explicitly.
    """
    entry = table.get(model)
    if entry is not None:
        return entry
    if default is not None:
        return default
    return PricingEntry()
