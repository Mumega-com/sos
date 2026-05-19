"""
ModelRouter — picks the best adapter for an agent and handles failover.
Every successful call debits the budget via the Economy service (HTTP).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type

from sos.adapters.base import AgentAdapter, ExecutionContext, ExecutionResult, UsageInfo
from sos.clients.economy import EconomyClient
from sos.observability.logging import get_logger

log = get_logger("adapter.router")

# Registry of all known providers — lazy so that missing SDKs (anthropic,
# google-genai, openai) only fail when a provider is actually dispatched to,
# not at import time. Each entry is (module_path, class_name).
_ADAPTER_PATHS: Dict[str, tuple[str, str]] = {
    "anthropic": ("sos.adapters.claude_adapter", "ClaudeAdapter"),
    "google": ("sos.adapters.gemini_adapter", "GeminiAdapter"),
    "openai": ("sos.adapters.openai_adapter", "OpenAIAdapter"),
}


def _resolve_adapter_class(provider: str) -> Type[AgentAdapter]:
    import importlib

    if provider not in _ADAPTER_PATHS:
        raise ValueError(f"Unknown provider: {provider!r}")
    module_path, attr = _ADAPTER_PATHS[provider]
    module = importlib.import_module(module_path)
    return getattr(module, attr)


class _AdapterRegistry(dict):
    """Dict view of adapter classes; resolves lazily via import on lookup."""

    def __getitem__(self, key):  # type: ignore[override]
        return _resolve_adapter_class(key)

    def get(self, key, default=None):  # type: ignore[override]
        try:
            return _resolve_adapter_class(key)
        except (ValueError, ImportError):
            return default

    def __contains__(self, key) -> bool:  # type: ignore[override]
        return key in _ADAPTER_PATHS

    def keys(self):  # type: ignore[override]
        return _ADAPTER_PATHS.keys()


ADAPTER_REGISTRY: Dict[str, Type[AgentAdapter]] = _AdapterRegistry()

# Default model preference per provider
PROVIDER_DEFAULT_MODELS: Dict[str, str] = {
    "anthropic": "claude-sonnet-4-5",
    "google": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
}


@dataclass
class AgentConfig:
    """
    Configuration for a single agent's model routing.

    primary_provider:   first-choice provider (e.g. "anthropic")
    primary_model:      specific model, or None to use provider default
    fallback_providers: ordered list of providers to try if primary fails
    budget_user_id:     wallet user_id to debit; defaults to agent_id
    """
    primary_provider: str = "anthropic"
    primary_model: Optional[str] = None
    fallback_providers: List[str] = field(default_factory=list)
    budget_user_id: Optional[str] = None


# Cost conversion: 1 cent = 10 RU (arbitrary internal rate)
_CENTS_TO_RU = 10.0


def _cents_to_ru(cents: int) -> float:
    return cents * _CENTS_TO_RU


class ModelRouter:
    """
    Selects and calls the right adapter for an agent.
    Falls back through the provider list on failure.
    Records cost via the Economy service HTTP client after each successful call.
    """

    def __init__(self, wallet: Optional[EconomyClient] = None):
        self._wallet = wallet or EconomyClient()
        # Instantiate one adapter per provider (singletons, lazy)
        self._adapters: Dict[str, AgentAdapter] = {}

    def _get_adapter(self, provider: str) -> AgentAdapter:
        if provider not in self._adapters:
            cls = ADAPTER_REGISTRY.get(provider)
            if cls is None:
                raise ValueError(f"Unknown provider: {provider!r}")
            self._adapters[provider] = cls()
        return self._adapters[provider]

    async def _record_cost(
        self,
        user_id: str,
        usage: UsageInfo,
        agent_id: str,
    ) -> None:
        """Debit the user's economy balance for the cost of this call."""
        ru = _cents_to_ru(usage.cost_cents)
        if ru <= 0:
            return
        reason = f"llm:{usage.provider}:{usage.model}:{agent_id}"
        try:
            await self._wallet.debit(user_id, ru, reason=reason)
        except Exception as exc:
            # Budget errors are non-fatal — log and continue
            log.warn(
                "Budget debit failed (non-fatal)",
                user_id=user_id,
                ru=ru,
                reason=reason,
                error=str(exc),
            )

    async def route(
        self,
        ctx: ExecutionContext,
        config: Optional[AgentConfig] = None,
    ) -> ExecutionResult:
        """
        Execute ctx using the best available adapter.

        If config is None, defaults to Anthropic Claude Sonnet with no fallback.
        """
        if config is None:
            config = AgentConfig()

        budget_user = config.budget_user_id or ctx.customer_id or ctx.agent_id

        # Build ordered provider list: primary first, then fallbacks
        providers = [config.primary_provider] + config.fallback_providers
        last_error: Optional[str] = None

        for provider in providers:
            try:
                adapter = self._get_adapter(provider)
            except ValueError as exc:
                log.warn("Skipping unknown provider", provider=provider, error=str(exc))
                last_error = str(exc)
                continue

            # If a primary model is specified, attach it for this provider only
            # when it matches the primary. Fallback providers use their own defaults.
            exec_ctx = ctx
            if provider == config.primary_provider and config.primary_model:
                # Shallow-clone the context with the overridden model
                exec_ctx = ExecutionContext(
                    agent_id=ctx.agent_id,
                    prompt=ctx.prompt,
                    system_prompt=ctx.system_prompt,
                    model=config.primary_model,
                    temperature=ctx.temperature,
                    max_tokens=ctx.max_tokens,
                    tools=ctx.tools,
                    session_id=ctx.session_id,
                    customer_id=ctx.customer_id,
                    project=ctx.project,
                    metadata=ctx.metadata,
                )
            elif exec_ctx.model is None:
                # Use the provider's default model
                default_model = PROVIDER_DEFAULT_MODELS.get(provider)
                exec_ctx = ExecutionContext(
                    agent_id=ctx.agent_id,
                    prompt=ctx.prompt,
                    system_prompt=ctx.system_prompt,
                    model=default_model,
                    temperature=ctx.temperature,
                    max_tokens=ctx.max_tokens,
                    tools=ctx.tools,
                    session_id=ctx.session_id,
                    customer_id=ctx.customer_id,
                    project=ctx.project,
                    metadata=ctx.metadata,
                )

            log.info(
                "Routing to provider",
                agent=ctx.agent_id,
                provider=provider,
                model=exec_ctx.model,
            )

            result = await adapter.execute(exec_ctx)

            if result.success:
                await self._record_cost(budget_user, result.usage, ctx.agent_id)
                return result

            last_error = result.error
            log.warn(
                "Provider failed, trying next",
                provider=provider,
                error=last_error,
                agent=ctx.agent_id,
            )

        # All providers exhausted
        log.error(
            "All providers failed",
            agent=ctx.agent_id,
            providers=providers,
            last_error=last_error,
        )
        return ExecutionResult(
            text="",
            usage=UsageInfo(),
            success=False,
            error=f"All providers failed. Last error: {last_error}",
        )

    async def health_check_all(self) -> Dict[str, bool]:
        """Run health checks for all registered providers in parallel."""
        providers = list(ADAPTER_REGISTRY.keys())

        async def check(provider: str) -> tuple[str, bool]:
            try:
                adapter = self._get_adapter(provider)
                ok = await adapter.health_check()
            except Exception as exc:
                log.warn("Health check error", provider=provider, error=str(exc))
                ok = False
            return provider, ok

        results = await asyncio.gather(*[check(p) for p in providers])
        return dict(results)
