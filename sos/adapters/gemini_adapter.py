"""
Gemini adapter — wraps Google GenerativeAI SDK.
Tracks token usage and reports cost per call.
"""
import os
from typing import Optional

import google.generativeai as genai
from google.api_core.exceptions import GoogleAPIError

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.adapters.pricing import PricingEntry, PricingTable, ensure_entry
from sos.observability.logging import get_logger

log = get_logger("adapter.gemini")

# Pricing catalog for Google Gemini + Imagen. Entries use `PricingEntry` so
# per-token (Gemini text/multimodal) and flat-per-call (Imagen) shapes coexist.
#
# Sources: https://ai.google.dev/pricing (verified 2026-04-17)
#          https://ai.google.dev/gemini-api/docs/models
# Callers should read from `PRICING` (the new canonical table). `MODEL_COSTS`
# is retained as a legacy-shape alias: entries are auto-projected to the
# (input_per_mtok, output_per_mtok) tuple Paperclip-era callers expect.
PRICING: PricingTable = {
    # --- Gemini 3 family (preview, 2026-04) ---
    "gemini-3-flash-preview":      PricingEntry(30,  125,  source="ai.google.dev pricing 2026-04-17 — preview tier"),
    "gemini-3.1-flash-lite-preview": PricingEntry(10,  40,  source="ai.google.dev pricing 2026-04-17 — preview tier"),

    # --- Gemini 2.5 family (stable) ---
    "gemini-2.5-pro":              PricingEntry(125, 1000, source="ai.google.dev pricing 2026-04-17"),
    "gemini-2.5-flash":            PricingEntry(30,  250,  source="ai.google.dev pricing 2026-04-17"),
    "gemini-2.5-flash-lite":       PricingEntry(10,  40,   source="ai.google.dev pricing 2026-04-17"),
    "gemini-flash-latest":         PricingEntry(30,  250,  source="alias → gemini-2.5-flash"),
    "gemini-flash-lite-latest":    PricingEntry(10,  40,   source="alias → gemini-2.5-flash-lite"),
    # Image generation via Gemini (flat per image on 1:1 square output)
    "gemini-2.5-flash-image":      PricingEntry(flat_cents_per_call=4,
                                               source="ai.google.dev image-gen 2026-04-17 — $0.04 per image"),

    # --- Gemini 2.0 family (stable) ---
    "gemini-2.0-flash":            PricingEntry(10,  40,   source="ai.google.dev pricing 2026-04-17"),
    "gemini-2.0-flash-lite":       PricingEntry(8,   30,   source="ai.google.dev pricing 2026-04-17"),

    # --- Gemini 1.5 family (still available, being phased out) ---
    "gemini-1.5-pro":              PricingEntry(125, 500,  source="ai.google.dev pricing 2026-04-17"),
    "gemini-1.5-flash":            PricingEntry(8,   30,   source="ai.google.dev pricing 2026-04-17"),
    "gemini-1.5-flash-8b":         PricingEntry(4,   15,   source="ai.google.dev pricing 2026-04-17"),

    # --- Imagen 4 family (flat-per-call; trop #97 specifically flagged these) ---
    "imagen-4.0-fast-generate-001":  PricingEntry(flat_cents_per_call=2,
                                                  source="ai.google.dev Imagen 4 2026-04-17 — $0.02/image"),
    "imagen-4.0-generate-001":       PricingEntry(flat_cents_per_call=4,
                                                  source="ai.google.dev Imagen 4 2026-04-17 — $0.04/image"),
    "imagen-4.0-ultra-generate-001": PricingEntry(flat_cents_per_call=6,
                                                  source="ai.google.dev Imagen 4 2026-04-17 — $0.06/image"),

    # --- Gemma open-weight family (free via Google AI Studio) ---
    "gemma-4-31b":                 PricingEntry(0, 0, source="open weights, zero direct cost"),
    "gemma-4-26b-moe":             PricingEntry(0, 0, source="open weights, zero direct cost"),
    "gemma-4-e4b":                 PricingEntry(0, 0, source="open weights, zero direct cost"),
    "gemma-4-e2b":                 PricingEntry(0, 0, source="open weights, zero direct cost"),
}

# Legacy-shape view for callers that expect (input_per_mtok, output_per_mtok).
# Flat-per-call entries are omitted here because the tuple shape can't
# represent them — callers using MODEL_COSTS must migrate to `PRICING` before
# they can handle image models.
MODEL_COSTS: dict[str, tuple[float, float]] = {
    name: (e.input_per_mtok, e.output_per_mtok)
    for name, e in PRICING.items()
    if not e.is_flat
}

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiAdapter(AgentAdapter):
    """Google Gemini adapter."""

    provider = "google"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._configured = False

    def _ensure_configured(self) -> None:
        if not self._configured:
            if not self._api_key:
                raise RuntimeError("GOOGLE_API_KEY is not set")
            genai.configure(api_key=self._api_key)
            self._configured = True

    def estimate_cost(self, input_tokens: int, output_tokens: int, model: str, image_count: int = 0) -> int:
        """Return estimated cost in cents (integer, rounded up).

        Unknown models now fall back to a zero-cost entry — the old Pro-tier
        default (125, 500) overestimated unknown models by up to 10×. If you
        need a conservative upper bound, add the model to PRICING explicitly.
        """
        entry = ensure_entry(PRICING, model)
        return entry.estimate_cents(input_tokens=input_tokens, output_tokens=output_tokens, image_count=image_count)

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        model_name = ctx.model or DEFAULT_MODEL
        self._ensure_configured()

        generation_config = genai.GenerationConfig(
            temperature=ctx.temperature,
            max_output_tokens=ctx.max_tokens,
        )

        system_instruction = ctx.system_prompt if ctx.system_prompt else None
        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=generation_config,
            system_instruction=system_instruction,
        )

        try:
            response = await model.generate_content_async(ctx.prompt)
        except GoogleAPIError as exc:
            log.error("Gemini API error", agent=ctx.agent_id, model=model_name, error=str(exc))
            return ExecutionResult(
                text="",
                usage=UsageInfo(model=model_name, provider=self.provider),
                success=False,
                error=str(exc),
            )
        except Exception as exc:
            log.error("Gemini unexpected error", agent=ctx.agent_id, model=model_name, error=str(exc))
            return ExecutionResult(
                text="",
                usage=UsageInfo(model=model_name, provider=self.provider),
                success=False,
                error=str(exc),
            )

        text = response.text if hasattr(response, "text") else ""

        # Usage metadata — present when available
        usage_meta = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
        cost_cents = self.estimate_cost(input_tokens, output_tokens, model_name)

        usage = UsageInfo(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            model=model_name,
            provider=self.provider,
        )

        log.info(
            "Gemini call complete",
            agent=ctx.agent_id,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
        )

        return ExecutionResult(
            text=text,
            usage=usage,
            success=True,
            session_id=ctx.session_id,
        )

    async def health_check(self) -> bool:
        try:
            self._ensure_configured()
            model = genai.GenerativeModel("gemini-2.0-flash-lite")
            await model.generate_content_async("ping")
            return True
        except Exception as exc:
            log.warning("Gemini health check failed", error=str(exc))
            return False
