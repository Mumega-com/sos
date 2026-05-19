"""
OpenAI adapter — wraps the OpenAI SDK.
Tracks token usage and reports cost per call.
"""
import os
from typing import Optional

from openai import AsyncOpenAI, APIError

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.adapters.pricing import PricingEntry, PricingTable, ensure_entry
from sos.observability.logging import get_logger

log = get_logger("adapter.openai")

# Pricing catalog for OpenAI. Sources:
#   https://openai.com/api/pricing (verified 2026-04-17)
#   https://platform.openai.com/docs/models
PRICING: PricingTable = {
    # --- GPT-5 family (current flagship) ---
    "gpt-5":                   PricingEntry(500, 2500, source="openai.com/api/pricing 2026-04-17 — GPT-5"),
    "gpt-5-mini":              PricingEntry(40,  200,  source="openai.com/api/pricing 2026-04-17 — GPT-5 mini"),
    "gpt-5-nano":              PricingEntry(15,  75,   source="openai.com/api/pricing 2026-04-17 — GPT-5 nano"),

    # --- GPT-4 family (previous generation, still widely used) ---
    "gpt-4o":                  PricingEntry(250,  1000, source="openai.com/api/pricing 2026-04-17"),
    "gpt-4o-mini":             PricingEntry(15,   60,   source="openai.com/api/pricing 2026-04-17"),
    "gpt-4-turbo":             PricingEntry(1000, 3000, source="openai.com/api/pricing 2026-04-17"),
    "gpt-4":                   PricingEntry(3000, 6000, source="openai.com/api/pricing 2026-04-17"),
    "gpt-3.5-turbo":           PricingEntry(50,   150,  source="openai.com/api/pricing 2026-04-17"),

    # --- Reasoning (o-series) ---
    "o1":                      PricingEntry(1500, 6000, source="openai.com/api/pricing 2026-04-17"),
    "o1-mini":                 PricingEntry(110,  440,  source="openai.com/api/pricing 2026-04-17"),
    "o3":                      PricingEntry(1000, 4000, source="openai.com/api/pricing 2026-04-17 — o3 full"),
    "o3-mini":                 PricingEntry(110,  440,  source="openai.com/api/pricing 2026-04-17"),
    "o4-mini":                 PricingEntry(110,  440,  source="openai.com/api/pricing 2026-04-17 — o4 mini"),

    # --- Image generation (flat-per-call) ---
    "dall-e-3":                PricingEntry(flat_cents_per_call=4,
                                           source="openai.com/api/pricing 2026-04-17 — DALL-E 3 standard 1024"),
    "gpt-image-1":             PricingEntry(flat_cents_per_call=4,
                                           source="openai.com/api/pricing 2026-04-17 — gpt-image-1 standard"),
}

# Legacy tuple-shape view. Flat entries (images) are omitted.
MODEL_COSTS: dict[str, tuple[float, float]] = {
    name: (e.input_per_mtok, e.output_per_mtok)
    for name, e in PRICING.items()
    if not e.is_flat
}

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIAdapter(AgentAdapter):
    """OpenAI GPT adapter."""

    provider = "openai"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    def estimate_cost(self, input_tokens: int, output_tokens: int, model: str) -> int:
        """Return estimated cost in cents (integer, rounded up).

        Unknown models fall back to a zero-cost entry — unknown models should
        be added to PRICING with a verified source, not silently given the
        GPT-4o default rate.
        """
        entry = ensure_entry(PRICING, model)
        return entry.estimate_cents(input_tokens=input_tokens, output_tokens=output_tokens)

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        model = ctx.model or DEFAULT_MODEL
        client = self._get_client()

        messages = []
        if ctx.system_prompt:
            messages.append({"role": "system", "content": ctx.system_prompt})
        messages.append({"role": "user", "content": ctx.prompt})

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": ctx.max_tokens,
            "temperature": ctx.temperature,
        }
        if ctx.tools:
            kwargs["tools"] = ctx.tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await client.chat.completions.create(**kwargs)
        except APIError as exc:
            log.error("OpenAI API error", agent=ctx.agent_id, model=model, error=str(exc))
            return ExecutionResult(
                text="",
                usage=UsageInfo(model=model, provider=self.provider),
                success=False,
                error=str(exc),
            )
        except Exception as exc:
            log.error("OpenAI unexpected error", agent=ctx.agent_id, model=model, error=str(exc))
            return ExecutionResult(
                text="",
                usage=UsageInfo(model=model, provider=self.provider),
                success=False,
                error=str(exc),
            )

        choice = response.choices[0]
        text = choice.message.content or ""

        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost_cents = self.estimate_cost(input_tokens, output_tokens, model)

        usage = UsageInfo(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            model=model,
            provider=self.provider,
        )

        log.info(
            "OpenAI call complete",
            agent=ctx.agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
        )

        return ExecutionResult(
            text=text,
            usage=usage,
            success=True,
            session_id=ctx.session_id,
            metadata={"finish_reason": choice.finish_reason},
        )

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            await client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception as exc:
            log.warning("OpenAI health check failed", error=str(exc))
            return False
