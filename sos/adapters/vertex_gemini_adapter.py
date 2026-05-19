"""
Vertex Gemini Adapter — Google Generative AI via Vertex AI (ADC billing).

Uses `google.genai` SDK with `vertexai=True` — authenticates via Application
Default Credentials (ADC), not an API key. Cost billed against Vertex AI
credits (GOOGLE_CLOUD_PROJECT), not the Gemini API quota.

This is the production billing path. Direct GeminiAdapter (API key) is
used for local dev when ADC is not configured.

Requires:
  - GOOGLE_CLOUD_PROJECT env var (defaults to 'mumega-com')
  - GOOGLE_CLOUD_LOCATION env var (defaults to 'us-central1')
  - ADC configured: `gcloud auth application-default login`
    or service account JSON at GOOGLE_APPLICATION_CREDENTIALS

Pricing is identical to GeminiAdapter — same model names, same token costs.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.adapters.gemini_adapter import PRICING, ensure_entry

log = logging.getLogger(__name__)

DEFAULT_MODEL = 'gemini-2.5-flash-lite'


class VertexGeminiAdapter(AgentAdapter):
    """
    Google Generative AI via Vertex AI — ADC auth, no API key.

    Drop-in replacement for GeminiAdapter. Same interface, same model names,
    Vertex billing path instead of API key billing.
    """

    provider = 'google-vertex'

    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        self._project = project or os.environ.get('GOOGLE_CLOUD_PROJECT', 'mumega-com')
        self._location = location or os.environ.get('GOOGLE_CLOUD_LOCATION', 'us-central1')
        self._client: Optional[object] = None

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client

        from google import genai

        self._client = genai.Client(
            vertexai=True,
            project=self._project,
            location=self._location,
        )
        log.info(
            'Vertex Gemini client initialised (project=%s, location=%s)',
            self._project,
            self._location,
        )
        return self._client

    def estimate_cost(self, input_tokens: int, output_tokens: int, model: str, image_count: int = 0) -> int:
        entry = ensure_entry(PRICING, model)
        return entry.estimate_cents(input_tokens=input_tokens, output_tokens=output_tokens, image_count=image_count)

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        from google import genai
        from google.genai import types

        model_name = ctx.model or DEFAULT_MODEL
        client = self._ensure_client()

        contents: list = []
        if ctx.system_prompt:
            contents.append(types.Content(
                role='user',
                parts=[types.Part(text=ctx.system_prompt + '\n\n' + ctx.prompt)],
            ))
        else:
            contents.append(types.Content(
                role='user',
                parts=[types.Part(text=ctx.prompt)],
            ))

        config = types.GenerateContentConfig(
            temperature=ctx.temperature,
            max_output_tokens=ctx.max_tokens,
        )

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            log.error('Vertex Gemini call failed: %s', exc)
            return ExecutionResult(
                text='',
                usage=UsageInfo(model=model_name, provider=self.provider),
                success=False,
                error=str(exc),
            )

        text = ''
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text'):
                    text += part.text

        usage_meta = getattr(response, 'usage_metadata', None)
        input_tokens = getattr(usage_meta, 'prompt_token_count', 0) or 0
        output_tokens = getattr(usage_meta, 'candidates_token_count', 0) or 0
        cost_cents = self.estimate_cost(input_tokens, output_tokens, model_name)

        usage = UsageInfo(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            model=model_name,
            provider=self.provider,
        )

        log.info(
            'Vertex Gemini call complete: model=%s in=%d out=%d cost_cents=%d',
            model_name, input_tokens, output_tokens, cost_cents,
        )

        return ExecutionResult(
            text=text,
            usage=usage,
            success=True,
            session_id=ctx.session_id,
        )

    async def health_check(self) -> bool:
        try:
            client = self._ensure_client()
            from google.genai import types
            response = client.models.generate_content(
                model='gemini-2.0-flash-lite',
                contents=[types.Content(role='user', parts=[types.Part(text='ping')])],
            )
            return bool(response.candidates)
        except Exception as exc:
            log.warning('Vertex Gemini health check failed: %s', exc)
            return False
