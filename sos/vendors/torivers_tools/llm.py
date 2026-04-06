"""
LLM client interface for automations.

This module provides a controlled interface for accessing LLM services
through the ToRivers platform proxy. Direct API access is not allowed
in sandboxed environments.

The SDK provides pre-configured clients that:
- Use platform-approved models
- Track token usage for billing
- Handle rate limiting
- Cache responses when appropriate
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

# =============================================================================
# Constants
# =============================================================================

# Chat completion models
APPROVED_CHAT_MODELS: list[str] = [
    # OpenAI models
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5-turbo",
    # Anthropic models
    "claude-sonnet-4-20250514",
    "claude-3-5-haiku-20241022",
    "claude-3-5-sonnet-20241022",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
    # OpenRouter-only (explicit allowlist)
    "google/gemma-3-27b-it:free",
]

# Embedding models
APPROVED_EMBEDDING_MODELS: list[str] = [
    "text-embedding-3-small",
    "text-embedding-3-large",
    "text-embedding-ada-002",
]

# All approved models (for backward compatibility)
APPROVED_MODELS: list[str] = APPROVED_CHAT_MODELS + APPROVED_EMBEDDING_MODELS

# Default models
DEFAULT_CHAT_MODEL: str = "gpt-4o-mini"
DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"

# Model type alias
ModelType = Literal["chat", "embedding"]


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class LLMMessage:
    """A message in an LLM conversation."""

    role: str  # "system", "user", or "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for API calls."""
        return {"role": self.role, "content": self.content}


@dataclass
class TokenUsage:
    """Token usage breakdown for an LLM request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> "TokenUsage":
        """Create TokenUsage from a dictionary."""
        return cls(
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
        )

    def to_dict(self) -> dict[str, int]:
        """Convert to dictionary."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class LLMResponse:
    """Response from an LLM request."""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"

    @property
    def tokens_used(self) -> int:
        """Total tokens used in the request (for billing)."""
        return self.usage.get("total_tokens", 0)

    @property
    def prompt_tokens(self) -> int:
        """Tokens used in the prompt."""
        return self.usage.get("prompt_tokens", 0)

    @property
    def completion_tokens(self) -> int:
        """Tokens used in the completion."""
        return self.usage.get("completion_tokens", 0)

    @property
    def token_usage(self) -> TokenUsage:
        """Get structured token usage."""
        return TokenUsage.from_dict(self.usage)


@dataclass
class EmbeddingResponse:
    """Response from an embedding request."""

    embeddings: list[list[float]]
    model: str
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def tokens_used(self) -> int:
        """Total tokens used in the request (for billing)."""
        return self.usage.get("total_tokens", 0)

    @property
    def dimensions(self) -> int:
        """Dimension of the embedding vectors."""
        if self.embeddings and len(self.embeddings) > 0:
            return len(self.embeddings[0])
        return 0


# =============================================================================
# Exceptions
# =============================================================================


class LLMError(Exception):
    """Base error from LLM service."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class ModelNotApprovedError(LLMError):
    """Raised when attempting to use a model not on the approved list."""

    def __init__(self, model: str, model_type: ModelType = "chat") -> None:
        approved = (
            APPROVED_CHAT_MODELS if model_type == "chat" else APPROVED_EMBEDDING_MODELS
        )
        message = (
            f"Model '{model}' is not approved for use. "
            f"Approved {model_type} models: {', '.join(approved)}"
        )
        super().__init__(message, code="model_not_approved", details={"model": model})
        self.model = model
        self.model_type = model_type


class RateLimitError(LLMError):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, code="rate_limit_exceeded")
        self.retry_after = retry_after


class TokenLimitExceededError(LLMError):
    """Raised when token limit is exceeded."""

    def __init__(
        self,
        message: str = "Token limit exceeded",
        tokens_requested: int | None = None,
        tokens_available: int | None = None,
    ) -> None:
        super().__init__(
            message,
            code="token_limit_exceeded",
            details={
                "tokens_requested": tokens_requested,
                "tokens_available": tokens_available,
            },
        )
        self.tokens_requested = tokens_requested
        self.tokens_available = tokens_available


# =============================================================================
# LLM Client Interface
# =============================================================================


class LLMClient(ABC):
    """
    Client interface for LLM services.

    Automations use this client to make LLM requests through the
    ToRivers proxy. This ensures:
    - Cost tracking and billing
    - Rate limiting
    - Content filtering
    - Audit logging

    The actual implementation is injected by the runtime.

    Example:
        async def analyze_text(self, state: MyState) -> dict:
            llm = LLMClient.get_default()

            response = await llm.chat([
                LLMMessage(role="system", content="You are a helpful assistant."),
                LLMMessage(role="user", content=f"Analyze: {state['input_data']['text']}")
            ])

            return {
                "output_data": {
                    "_version": 1,
                    "_blocks": [
                        {
                            "type": "text",
                            "label": "Analysis",
                            "content": response.content,
                            "format": "markdown",
                        }
                    ],
                }
            }
    """

    # Class-level approved models (for subclass reference)
    APPROVED_CHAT_MODELS = APPROVED_CHAT_MODELS
    APPROVED_EMBEDDING_MODELS = APPROVED_EMBEDDING_MODELS
    APPROVED_MODELS = APPROVED_MODELS

    @classmethod
    def validate_model(cls, model: str, model_type: ModelType = "chat") -> None:
        """
        Validate that a model is approved for use.

        Args:
            model: Model name to validate
            model_type: Type of model ("chat" or "embedding")

        Raises:
            ModelNotApprovedError: If model is not on the approved list
        """
        approved = (
            APPROVED_CHAT_MODELS if model_type == "chat" else APPROVED_EMBEDDING_MODELS
        )
        if model not in approved:
            raise ModelNotApprovedError(model, model_type)

    @classmethod
    def is_model_approved(cls, model: str, model_type: ModelType = "chat") -> bool:
        """
        Check if a model is approved for use.

        Args:
            model: Model name to check
            model_type: Type of model ("chat" or "embedding")

        Returns:
            True if model is approved, False otherwise
        """
        approved = (
            APPROVED_CHAT_MODELS if model_type == "chat" else APPROVED_EMBEDDING_MODELS
        )
        return model in approved

    @classmethod
    def get_default_model(cls, model_type: ModelType = "chat") -> str:
        """
        Get the default model for a given type.

        Args:
            model_type: Type of model ("chat" or "embedding")

        Returns:
            Default model name
        """
        return DEFAULT_CHAT_MODEL if model_type == "chat" else DEFAULT_EMBEDDING_MODEL

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of messages in the conversation
            model: Model to use (if None, uses default)
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response

        Returns:
            LLM response with content and usage info

        Raises:
            LLMError: If the request fails
            ModelNotApprovedError: If model is not approved
        """
        pass

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """
        Send a text completion request.

        Args:
            prompt: The prompt to complete
            model: Model to use (if None, uses default)
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response

        Returns:
            LLM response with content and usage info

        Raises:
            LLMError: If the request fails
            ModelNotApprovedError: If model is not approved
        """
        pass

    @abstractmethod
    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        """
        Generate embeddings for texts.

        Args:
            texts: List of texts to embed
            model: Embedding model to use

        Returns:
            List of embedding vectors

        Raises:
            LLMError: If the request fails
            ModelNotApprovedError: If model is not approved
        """
        pass

    async def embed_with_metadata(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> EmbeddingResponse:
        """
        Generate embeddings for texts with usage metadata.

        This is a convenience method that returns full metadata
        including token usage.

        Args:
            texts: List of texts to embed
            model: Embedding model to use

        Returns:
            EmbeddingResponse with embeddings and metadata

        Raises:
            LLMError: If the request fails
            ModelNotApprovedError: If model is not approved
        """
        embeddings = await self.embed(texts, model)
        return EmbeddingResponse(
            embeddings=embeddings,
            model=model or DEFAULT_EMBEDDING_MODEL,
            usage={"total_tokens": 0},  # Override in implementations
        )

    @staticmethod
    def get_default() -> "LLMClient":
        """
        Get the default LLM client.

        When ``TORIVERS_LLM_PROXY_URL`` and ``TORIVERS_LLM_TOKEN`` environment
        variables are set, returns an :class:`HttpLLMClient` backed by the LLM
        proxy. For testing, use :class:`torivers_sdk.testing.mocks.MockLLMClient`.

        Returns:
            Default LLM client instance

        Raises:
            RuntimeError: If called outside runtime environment
        """
        base_url = os.environ.get("TORIVERS_LLM_PROXY_URL")
        token = os.environ.get("TORIVERS_LLM_TOKEN")
        if base_url and token:
            return HttpLLMClient(base_url=base_url, token=token)

        raise RuntimeError(
            "LLMClient.get_default() can only be called in the runtime environment. "
            "Set TORIVERS_LLM_PROXY_URL and TORIVERS_LLM_TOKEN, or "
            "use torivers_sdk.testing.mocks.MockLLMClient for testing."
        )


# =============================================================================
# HTTP LLM Client (concrete implementation)
# =============================================================================


class HttpLLMClient(LLMClient):
    """
    LLM client backed by the LLM proxy REST API.

    Sends authenticated requests to the proxy's ``/api/llm/*`` endpoints using
    short-lived JWT tokens for execution-scoped LLM operations.

    Args:
        base_url: LLM proxy base URL (e.g. ``http://localhost:8001``).
        token: JWT token for authentication.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "HttpLLMClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # -- helpers --------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def _request(
        self,
        method: str,
        url_path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        client = self._get_client()
        try:
            return await client.request(
                method,
                self._url(url_path),
                headers=self._headers(),
                **kwargs,
            )
        except httpx.TimeoutException:
            raise LLMError("LLM request timed out", code="timeout")
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM connection error: {exc}", code="connection_error")

    def _raise_for_status(
        self,
        response: httpx.Response,
        *,
        model: str,
        model_type: ModelType,
    ) -> None:
        if response.is_success:
            return

        detail = ""
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text

        status = response.status_code
        if status == 400:
            raise ModelNotApprovedError(model=model, model_type=model_type)
        if status == 401:
            raise LLMError(detail or "LLM access denied", code="access_denied")
        if status == 429:
            retry_after = None
            if "Retry-After" in response.headers:
                try:
                    retry_after = float(response.headers["Retry-After"])
                except Exception:
                    retry_after = None
            if retry_after is not None:
                raise RateLimitError(detail or "Rate limit exceeded", retry_after)
            raise TokenLimitExceededError(detail or "Token limit exceeded")

        raise LLMError(
            detail or f"LLM request failed (HTTP {status})", code=str(status)
        )

    # -- LLMClient ------------------------------------------------------------

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        resolved_model = model or DEFAULT_CHAT_MODEL
        self.validate_model(resolved_model, "chat")

        payload: dict[str, Any] = {
            "messages": [m.to_dict() for m in messages],
            "model": resolved_model,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        response = await self._request("POST", "/api/llm/chat", json=payload)
        self._raise_for_status(response, model=resolved_model, model_type="chat")

        data = response.json()
        return LLMResponse(
            content=data.get("content", ""),
            model=data.get("model", resolved_model),
            usage=data.get("usage", {}) or {},
            finish_reason=data.get("finish_reason", "stop") or "stop",
        )

    async def complete(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return await self.chat(
            messages=[LLMMessage(role="user", content=prompt)],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        resolved_model = model or DEFAULT_EMBEDDING_MODEL
        self.validate_model(resolved_model, "embedding")

        payload: dict[str, Any] = {
            "texts": texts,
            "model": resolved_model,
        }
        response = await self._request("POST", "/api/llm/embed", json=payload)
        self._raise_for_status(response, model=resolved_model, model_type="embedding")

        return response.json().get("embeddings", [])

    async def embed_with_metadata(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> EmbeddingResponse:
        resolved_model = model or DEFAULT_EMBEDDING_MODEL
        self.validate_model(resolved_model, "embedding")

        payload: dict[str, Any] = {
            "texts": texts,
            "model": resolved_model,
        }
        response = await self._request("POST", "/api/llm/embed", json=payload)
        self._raise_for_status(response, model=resolved_model, model_type="embedding")

        data = response.json()
        return EmbeddingResponse(
            embeddings=data.get("embeddings", []),
            model=data.get("model", resolved_model),
            usage=data.get("usage", {}) or {},
        )
