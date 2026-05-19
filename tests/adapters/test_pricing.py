"""Tests for sos.adapters.pricing — covers trop issue #97 fix.

Verifies:
  - Per-token pricing math matches the old adapter formula on the common path.
  - Flat-per-call pricing correctly handles image models (Imagen 4, DALL-E 3).
  - Unknown models return zero cost instead of the old punitive default.
  - `estimate_micros()` preserves precision for sub-cent calls.
"""
from __future__ import annotations

from sos.adapters.pricing import PricingEntry, ensure_entry


class TestPerTokenPricing:
    def test_basic_math(self):
        # Sonnet-4.6: 300c/Mtok in, 1500c/Mtok out.
        entry = PricingEntry(300, 1500)
        # 10k in, 5k out → (10k*300 + 5k*1500)/1M = (3M + 7.5M)/1M = 10.5 cents → 11 (ceil)
        assert entry.estimate_cents(10_000, 5_000) == 11

    def test_rounds_up_small_costs(self):
        # Flash Lite: 10c/Mtok in, 40c/Mtok out.
        # 100 in + 50 out → (1000 + 2000)/1M = 0.003 cents → 1 (floor to min 1 for non-zero)
        entry = PricingEntry(10, 40)
        assert entry.estimate_cents(100, 50) == 1

    def test_zero_tokens_zero_cost(self):
        entry = PricingEntry(300, 1500)
        assert entry.estimate_cents(0, 0) == 0

    def test_free_tier_zero_rates(self):
        # Gemma: open weights, zero cost.
        entry = PricingEntry(0, 0)
        assert entry.estimate_cents(1_000_000, 1_000_000) == 0


class TestFlatPerCallPricing:
    def test_imagen_4_standard(self):
        # Imagen 4 standard: 4c/image flat.
        entry = PricingEntry(flat_cents_per_call=4)
        assert entry.is_flat
        # image_count=1 (default when 0) → 4c
        assert entry.estimate_cents(image_count=1) == 4
        assert entry.estimate_cents(image_count=3) == 12

    def test_imagen_4_ultra(self):
        entry = PricingEntry(flat_cents_per_call=6)
        assert entry.estimate_cents(image_count=5) == 30

    def test_flat_ignores_tokens(self):
        # Even if tokens are passed, a flat entry bills per-image.
        entry = PricingEntry(flat_cents_per_call=4)
        assert entry.estimate_cents(input_tokens=999_999, output_tokens=999_999, image_count=2) == 8


class TestUnknownModel:
    def test_unknown_returns_zero(self):
        # Core trop #97 bug: unknown models used to get (125, 500) Pro-tier
        # default. Now they fall back to an empty entry → 0 cents.
        entry = ensure_entry({}, "some-model-we-have-never-heard-of")
        assert entry.estimate_cents(1_000_000, 1_000_000) == 0

    def test_unknown_honors_explicit_default(self):
        fallback = PricingEntry(100, 500)
        entry = ensure_entry({}, "mystery-model", default=fallback)
        assert entry.estimate_cents(1_000_000, 1_000_000) == 600


class TestMicrosPrecision:
    def test_sub_cent_call(self):
        # A single Gemini Flash Lite call on small prompt: well below 1 cent.
        # 150 in + 60 out at (10, 40) → (1500 + 2400)/1M = 0.0039 cents → 39 micros
        entry = PricingEntry(10, 40)
        assert entry.estimate_micros(150, 60) == 39
        # Same call in cents rounds up to 1 (min non-zero)
        assert entry.estimate_cents(150, 60) == 1

    def test_flat_call_micros(self):
        # Imagen 4 standard: 4c = 40k micros per image.
        entry = PricingEntry(flat_cents_per_call=4)
        assert entry.estimate_micros(image_count=1) == 40_000


class TestLiveCatalogs:
    """Smoke checks against the actual shipped catalogs."""

    def test_gemini_catalog_has_imagen_4(self):
        from sos.adapters.gemini_adapter import PRICING as GEM
        assert "imagen-4.0-generate-001" in GEM
        assert GEM["imagen-4.0-generate-001"].is_flat
        # Trop-specific: the three Imagen 4 tiers are all present.
        assert "imagen-4.0-fast-generate-001" in GEM
        assert "imagen-4.0-ultra-generate-001" in GEM

    def test_gemini_catalog_has_25_flash(self):
        from sos.adapters.gemini_adapter import PRICING as GEM
        # trop #97 specifically flagged gemini-2.5-flash as missing.
        assert "gemini-2.5-flash" in GEM
        assert "gemini-2.5-flash-lite" in GEM

    def test_claude_catalog_has_current_opus(self):
        from sos.adapters.claude_adapter import PRICING as CLA
        # Opus 4.7 is the current flagship per ~/.claude/CLAUDE.md.
        assert "claude-opus-4-7" in CLA
        assert "claude-sonnet-4-6" in CLA
        assert "claude-haiku-4-5" in CLA

    def test_openai_catalog_has_gpt_5(self):
        from sos.adapters.openai_adapter import PRICING as OAI
        assert "gpt-5" in OAI
        assert "gpt-5-mini" in OAI

    def test_every_entry_has_source(self):
        """Every non-zero entry should carry a source link (trop audit lever)."""
        from sos.adapters.gemini_adapter import PRICING as GEM
        from sos.adapters.claude_adapter import PRICING as CLA
        from sos.adapters.openai_adapter import PRICING as OAI
        for name, e in {**GEM, **CLA, **OAI}.items():
            # Free-tier entries (Gemma) may have a source but zero rates; others must have a source.
            if e.input_per_mtok > 0 or e.output_per_mtok > 0 or e.flat_cents_per_call > 0:
                assert e.source, f"pricing entry {name!r} missing source"
