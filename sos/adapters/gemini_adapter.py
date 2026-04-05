"""
Gemini adapter — wraps Google GenerativeAI SDK.
Tracks token usage and reports cost per call.
"""
import os
from typing import Optional

import google.generativeai as genai
from google.api_core.exceptions import GoogleAPIError

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.observability.logging import get_logger

log = get_logger("adapter.gemini")

# Cost in cents per 1M tokens (input, output)
# Gemini pricing as of 2025 (context <= 128k tier)
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gemini-2.5-pro":          (125,  1000),
    "gemini-2.0-flash":        (10,   40),
    "gemini-2.0-flash-lite":   (8,    30),
    "gemini-1.5-pro":          (125,  500),
    "gemini-1.5-flash":        (8,    30),
    "gemini-1.5-flash-8b":     (4,    15),
    # Gemma 4 — Released April 2, 2026 via Google AI Studio
    "gemma-4-31b":             (0,    0), # Free tier available
    "gemma-4-26b-moe":         (0,    0),
    "gemma-4-e4b":             (0,    0),
    "gemma-4-e2b":             (0,    0),
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

    def estimate_cost(self, input_tokens: int, output_tokens: int, model: str) -> int:
        """Return estimated cost in cents (integer, rounded up)."""
        in_rate, out_rate = MODEL_COSTS.get(model, (125, 500))
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
        return max(1, int(cost)) if cost > 0 else 0

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
