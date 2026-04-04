"""
Claude adapter — wraps the Anthropic SDK.
Tracks token usage and reports cost per call.
"""
import os
from typing import Optional

import anthropic

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.observability.logging import get_logger

log = get_logger("adapter.claude")

# Cost in cents per 1M tokens (input, output)
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-opus-4-5":         (1500, 7500),
    "claude-sonnet-4-5":       (300,  1500),
    "claude-haiku-3-5":        (80,   400),
    "claude-3-opus-20240229":  (1500, 7500),
    "claude-3-5-sonnet-20241022": (300, 1500),
    "claude-3-5-haiku-20241022":  (80,  400),
}

DEFAULT_MODEL = "claude-sonnet-4-5"


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
        """Return estimated cost in cents (integer, rounded up)."""
        in_rate, out_rate = MODEL_COSTS.get(model, (300, 1500))
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
        return max(1, int(cost)) if cost > 0 else 0

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
