"""
OpenAI adapter — wraps the OpenAI SDK.
Tracks token usage and reports cost per call.
"""
import os
from typing import Optional

from openai import AsyncOpenAI, APIError

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.observability.logging import get_logger

log = get_logger("adapter.openai")

# Cost in cents per 1M tokens (input, output)
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o":                  (250,  1000),
    "gpt-4o-mini":             (15,   60),
    "gpt-4-turbo":             (1000, 3000),
    "gpt-4":                   (3000, 6000),
    "gpt-3.5-turbo":           (50,   150),
    "o1":                      (1500, 6000),
    "o1-mini":                 (110,  440),
    "o3-mini":                 (110,  440),
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
        """Return estimated cost in cents (integer, rounded up)."""
        in_rate, out_rate = MODEL_COSTS.get(model, (250, 1000))
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
        return max(1, int(cost)) if cost > 0 else 0

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
