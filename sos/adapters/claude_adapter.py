"""
Claude adapter — wraps the Anthropic SDK.
Tracks token usage and reports cost per call.
"""
import os
from typing import Optional

import anthropic

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.adapters.pricing import PricingEntry, PricingTable, ensure_entry
from sos.observability.logging import get_logger

log = get_logger("adapter.claude")

# Pricing catalog for Anthropic Claude. Sources:
#   https://docs.anthropic.com/en/docs/about-claude/pricing (verified 2026-04-17)
#   Model IDs cross-checked against https://docs.anthropic.com/en/docs/about-claude/models/overview
PRICING: PricingTable = {
    # --- Claude 4.x family (current) ---
    "claude-opus-4-7":             PricingEntry(1500, 7500, source="docs.anthropic.com pricing 2026-04-17 — Opus 4.7"),
    "claude-opus-4-6":             PricingEntry(1500, 7500, source="docs.anthropic.com pricing 2026-04-17 — Opus 4.6"),
    "claude-sonnet-4-6":           PricingEntry(300,  1500, source="docs.anthropic.com pricing 2026-04-17 — Sonnet 4.6"),
    "claude-haiku-4-5-20251001":   PricingEntry(80,   400,  source="docs.anthropic.com pricing 2026-04-17 — Haiku 4.5"),
    "claude-haiku-4-5":            PricingEntry(80,   400,  source="alias → claude-haiku-4-5-20251001"),

    # --- Claude 4.x previous ---
    "claude-opus-4-5":             PricingEntry(1500, 7500, source="docs.anthropic.com pricing 2026-04-17"),
    "claude-sonnet-4-5":           PricingEntry(300,  1500, source="docs.anthropic.com pricing 2026-04-17"),
    "claude-haiku-3-5":            PricingEntry(80,   400,  source="docs.anthropic.com pricing 2026-04-17"),

    # --- Claude 3.x legacy ---
    "claude-3-opus-20240229":       PricingEntry(1500, 7500, source="docs.anthropic.com pricing 2026-04-17 — 3.0 legacy"),
    "claude-3-5-sonnet-20241022":   PricingEntry(300,  1500, source="docs.anthropic.com pricing 2026-04-17 — 3.5 legacy"),
    "claude-3-5-haiku-20241022":    PricingEntry(80,   400,  source="docs.anthropic.com pricing 2026-04-17 — 3.5 legacy"),
}

# Legacy-shape view (tuple-only) for Paperclip-era callers.
MODEL_COSTS: dict[str, tuple[float, float]] = {
    name: (e.input_per_mtok, e.output_per_mtok)
    for name, e in PRICING.items()
    if not e.is_flat
}

DEFAULT_MODEL = "claude-sonnet-4-6"


class ClaudeAdapter(AgentAdapter):
    """Anthropic Claude adapter."""

    provider = "anthropic"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client: Optional[anthropic.AsyncAnthropic] = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    def estimate_cost(self, input_tokens: int, output_tokens: int, model: str) -> int:
        """Return estimated cost in cents (integer, rounded up).

        Unknown models fall back to a zero-cost entry instead of the prior
        Sonnet-tier default — unknown models should be explicitly listed in
        PRICING rather than assigned a punitive default rate.
        """
        entry = ensure_entry(PRICING, model)
        return entry.estimate_cents(input_tokens=input_tokens, output_tokens=output_tokens)

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        model = ctx.model or DEFAULT_MODEL
        client = self._get_client()

        kwargs: dict = {
            "model": model,
            "max_tokens": ctx.max_tokens,
            "temperature": ctx.temperature,
            "messages": [{"role": "user", "content": ctx.prompt}],
        }
        if ctx.system_prompt:
            kwargs["system"] = ctx.system_prompt
        if ctx.tools:
            kwargs["tools"] = ctx.tools

        try:
            response = await client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            log.error("Claude API error", agent=ctx.agent_id, model=model, error=str(exc))
            return ExecutionResult(
                text="",
                usage=UsageInfo(model=model, provider=self.provider),
                success=False,
                error=str(exc),
            )

        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_cents = self.estimate_cost(input_tokens, output_tokens, model)

        usage = UsageInfo(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            model=model,
            provider=self.provider,
        )

        log.info(
            "Claude call complete",
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
        )

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            # Minimal call to confirm auth and reachability
            await client.messages.create(
                model="claude-haiku-3-5",
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception as exc:
            log.warning("Claude health check failed", error=str(exc))
            return False
