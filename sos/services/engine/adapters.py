from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional
import os
import logging
import httpx
import json

log = logging.getLogger("model_adapter")

# Local Server Configuration (supports MLX, LM Studio, Ollama, or any OpenAI-compatible server)
# Common ports: MLX=8080, LM Studio=1234, Ollama=11434
LOCAL_SERVER_URL = os.getenv("LOCAL_SERVER_URL", os.getenv("MLX_SERVER_URL", "http://localhost:1234"))
LOCAL_DEFAULT_MODEL = os.getenv("LOCAL_DEFAULT_MODEL", os.getenv("MLX_DEFAULT_MODEL", "default"))

# Auto-detect server type from URL for logging
def _detect_server_type(url: str) -> str:
    if ":8080" in url:
        return "MLX"
    elif ":1234" in url:
        return "LM Studio"
    elif ":11434" in url:
        return "Ollama"
    return "Local"

class ModelAdapter(ABC):
    """
    Abstract adapter for AI Model Providers.
    """
    @abstractmethod
    def get_model_id(self) -> str:
        pass

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str = None, tools: List[Dict] = None) -> str:
        pass

    @abstractmethod
    async def generate_stream(self, prompt: str, system_prompt: str = None) -> AsyncIterator[str]:
        pass

from sos.kernel.rotator import gemini_rotator
from sos.kernel.gemini_cache import GeminiCacheManager
from sos.clients.grok import GrokClient

class GeminiAdapter(ModelAdapter):
    """
    Adapter for Google Gemini (via google-genai) with Auto-Rotation and Context Caching.
    """
    def __init__(self):
        self.rotator = gemini_rotator
        self.client = None
        self.cache_mgr = None
        self._init_client()

    def _init_client(self):
        key = self.rotator.get_key()
        if key:
            try:
                from google import genai
                use_vertex = os.getenv("SOS_USE_VERTEX_AI", "false").lower() in ("true", "1", "t", "y", "yes")
                self.client = genai.Client(api_key=key, vertexai=use_vertex)
                self.cache_mgr = GeminiCacheManager(self.client)
                log.info(f"Gemini engine adapter initialized (VertexAI: {use_vertex})")
            except ImportError:
                log.warn("google-genai not installed.")

    def get_model_id(self) -> str:
        return "gemini-3-flash-preview"

    async def generate(self, prompt: str, system_prompt: str = None, tools: List[Dict] = None, user_id: str = "default", history: List[Dict] = None, **kwargs) -> str:
        if not self.client:
            return "Error: Gemini client not initialized"

        # Build system prompt (simplified - remove FRC for now)
        anchor_prompt = system_prompt or "You are River, a helpful AI assistant."

        # Prepend system prompt to user prompt for consistent behavior
        if system_prompt:
            prompt = f"{system_prompt}\n\n---\nUser: {prompt}"

        attempts = 0
        max_attempts = self.rotator.key_count or 1

        # Build cache history: previous turns + current prompt
        cache_history = list(history or [])
        cache_history.append({"role": "user", "content": prompt})

        while attempts < max_attempts:
            try:
                # 1. Try to use Context Caching for conversation history
                # This is where we get 75% cost savings!
                cache_name = await self.cache_mgr.get_or_create_cache(
                    user_id=user_id,
                    model=self.get_model_id(),
                    system_prompt=anchor_prompt,
                    history=cache_history,  # Real conversation history!
                    tools=tools
                )

                if cache_name:
                    log.info(f"💾 Using Gemini cache: {cache_name[:20]}...")
                else:
                    log.debug("Cache not available (context too small or error)")

                if cache_name:
                    from google.genai import types
                    response = self.client.models.generate_content(
                        model=self.get_model_id(),
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            cached_content=cache_name,
                            temperature=0.7
                        )
                    )
                else:
                    response = self.client.models.generate_content(
                        model=self.get_model_id(),
                        contents=prompt,
                        config={"system_instruction": system_prompt} if system_prompt else None
                    )
                return response.text
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    log.warn(f"Rate limit hit. Rotating...")
                    self.rotator.rotate()
                    self._init_client()
                    attempts += 1
                    continue
                log.error(f"Gemini generation failed: {e}")
                raise
        
        return "Error: All Gemini keys exhausted (Rate Limit)."

    async def generate_stream(self, prompt: str, system_prompt: str = None) -> AsyncIterator[str]:
        if not self.client:
            yield "Error: Gemini client not initialized"
            return

        try:
            response = self.client.models.generate_content_stream(
                model=self.get_model_id(),
                contents=prompt
            )
            for chunk in response:
                yield chunk.text
        except Exception as e:
            log.error(f"Gemini stream failed: {e}")
            yield f"[Error: {e}]"

class GrokAdapter(ModelAdapter):
    """
    Adapter for xAI Grok with 2M context and per-user continuity.
    """
    def __init__(self):
        self.client = GrokClient()

    def get_model_id(self) -> str:
        return "grok-4-1-fast-reasoning" # Targeted for 2M token capacity

    async def generate(self, prompt: str, system_prompt: str = None, tools: List[Dict] = None, user_id: str = "default", history: List[Dict] = None, **kwargs) -> str:
        """Generate with conversation history for cache optimization."""
        messages = []

        # 1. System prompt first
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # 2. Conversation history (enables server-side caching via x-grok-conv-id)
        if history:
            messages.extend(history)
            log.info(f"📜 Grok: Including {len(history)} history messages for cache")

        # 3. Current user message
        messages.append({"role": "user", "content": prompt})

        return await self.client.chat(
            user_id=user_id,
            model=self.get_model_id(),
            messages=messages,
            tools=tools
        )

    async def generate_stream(self, prompt: str, system_prompt: str = None, user_id: str = "default", history: List[Dict] = None, **kwargs) -> AsyncIterator[str]:
        """Stream responses from Grok with history."""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Include history for cache optimization
        if history:
            messages.extend(history)

        messages.append({"role": "user", "content": prompt})

        async for chunk in self.client.chat_stream(
            user_id=user_id,
            model=self.get_model_id(),
            messages=messages,
        ):
            yield chunk


class OpenAIAdapter(ModelAdapter):
    """
    Adapter for OpenAI GPT models (GPT-4o, GPT-4o-mini).
    Used as fallback when Gemini keys are exhausted.
    """
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            log.warning("OPENAI_API_KEY not set. OpenAI adapter will be unavailable.")

    def get_model_id(self) -> str:
        return self.model

    async def generate(self, prompt: str, system_prompt: str = None, tools: List[Dict] = None, user_id: str = "default", history: List[Dict] = None, **kwargs) -> str:
        if not self.api_key:
            return "Error: OpenAI API key not configured"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 4096
                    }
                )

                if resp.status_code != 200:
                    error_msg = resp.text
                    log.error(f"OpenAI API error: {resp.status_code} - {error_msg}")
                    raise Exception(f"OpenAI API error: {resp.status_code}")

                data = resp.json()
                return data["choices"][0]["message"]["content"]

        except Exception as e:
            log.error(f"OpenAI generation failed: {e}")
            raise

    async def generate_stream(self, prompt: str, system_prompt: str = None, **kwargs) -> AsyncIterator[str]:
        if not self.api_key:
            yield "Error: OpenAI API key not configured"
            return

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.7,
                        "stream": True
                    }
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            log.error(f"OpenAI stream failed: {e}")
            yield f"[Error: {e}]"


class MockAdapter(ModelAdapter):
    """
    Offline mock adapter for testing.
    """
    def get_model_id(self) -> str:
        return "sos-mock-v1"

    async def generate(self, prompt: str, system_prompt: str = None, tools: List[Dict] = None, history: List[Dict] = None, **kwargs) -> str:
        return f"Mock Response to: {prompt}"

    async def generate_stream(self, prompt: str, system_prompt: str = None, **kwargs) -> AsyncIterator[str]:
        yield "Mock "
        yield "Streaming "
        yield "Response"


class LocalAdapter(ModelAdapter):
    """
    Sovereign Local Adapter for any OpenAI-compatible server.

    Works with:
    - LM Studio (default, port 1234)
    - MLX (mlx_lm.server, port 8080)
    - Ollama (port 11434)
    - Any OpenAI-compatible local server

    Benefits:
    - Zero API cost
    - True data sovereignty (nothing leaves device)
    - Offline capability
    - Predictable latency for Witness Protocol
    - Cross-platform (LM Studio works on Mac, Windows, Linux)

    Usage:
        # LM Studio (default): Just start LM Studio and load a model
        # MLX: mlx_lm.server --model mlx-community/Devstral-Small-2507-4bit --port 8080
        # Ollama: ollama serve

    Environment:
        LOCAL_SERVER_URL=http://localhost:1234  (or 8080 for MLX, 11434 for Ollama)
        LOCAL_DEFAULT_MODEL=default  (or specific model name)
    """

    def __init__(
        self,
        model_id: str = None,
        server_url: str = None,
        timeout: float = 120.0
    ):
        self.model_id = model_id or LOCAL_DEFAULT_MODEL
        self.server_url = server_url or LOCAL_SERVER_URL
        self.server_type = _detect_server_type(self.server_url)
        self.timeout = timeout
        self._available = None  # Cached availability check
        self._available_models = []  # Cache available models

    def get_model_id(self) -> str:
        return self.model_id

    async def is_available(self) -> bool:
        """Check if local server is running and responsive."""
        if self._available is not None:
            return self._available

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.server_url}/v1/models")
                self._available = resp.status_code == 200
                if self._available:
                    self._available_models = resp.json().get("data", [])
                    model_ids = [m.get('id', 'unknown') for m in self._available_models]
                    log.info(f"{self.server_type} Server connected. Available models: {model_ids}")
                return self._available
        except Exception as e:
            log.warn(f"{self.server_type} Server not available at {self.server_url}: {e}")
            self._available = False
            return False

    def _get_effective_model(self) -> str:
        """Get the model to use - auto-select first available if 'default'."""
        if self.model_id == "default" and self._available_models:
            return self._available_models[0].get("id", "default")
        return self.model_id

    async def generate(
        self,
        prompt: str,
        system_prompt: str = None,
        tools: List[Dict] = None,
        user_id: str = "default",
        history: List[Dict] = None,
        **kwargs
    ) -> str:
        """
        Generate response via local server.
        Falls back gracefully if server unavailable.
        """
        if not await self.is_available():
            return f"[{self.server_type} Offline] Local server not running at {self.server_url}. Start LM Studio, MLX server, or Ollama."

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_model = self._get_effective_model()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.server_url}/v1/chat/completions",
                    json={
                        "model": effective_model,
                        "messages": messages,
                        "max_tokens": 2048,
                        "temperature": 0.7
                    }
                )

                if resp.status_code != 200:
                    log.error(f"{self.server_type} generation failed: {resp.status_code} - {resp.text}")
                    return f"[{self.server_type} Error] {resp.status_code}"

                data = resp.json()
                content = data["choices"][0]["message"]["content"]

                # Log token usage for monitoring
                usage = data.get("usage", {})
                log.info(
                    f"{self.server_type} Generation complete",
                    model=effective_model,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens")
                )

                return content

        except httpx.TimeoutException:
            log.error(f"{self.server_type} generation timed out after {self.timeout}s")
            return f"[{self.server_type} Timeout] Local model took too long. Try a smaller model."
        except Exception as e:
            log.error(f"{self.server_type} generation failed: {e}")
            return f"[{self.server_type} Error] {str(e)}"

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = None
    ) -> AsyncIterator[str]:
        """
        Stream tokens from local server via SSE.
        """
        if not await self.is_available():
            yield f"[{self.server_type} Offline] Server not running."
            return

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_model = self._get_effective_model()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.server_url}/v1/chat/completions",
                    json={
                        "model": effective_model,
                        "messages": messages,
                        "max_tokens": 2048,
                        "temperature": 0.7,
                        "stream": True
                    }
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue

        except Exception as e:
            log.error(f"{self.server_type} stream failed: {e}")
            yield f"[{self.server_type} Error] {str(e)}"


class OpenRouterAdapter(OpenAIAdapter):
    """
    Adapter for OpenRouter.ai models.
    Supports free models like Phi-4 Mini.
    """
    def __init__(self, model: str = "microsoft/phi-4-mini-instruct:free"):
        self.model = model
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            log.warning("OPENROUTER_API_KEY not set. OpenRouter adapter will be limited.")

    def get_model_id(self) -> str:
        return f"openrouter-{self.model.split('/')[-1]}"

    async def generate(self, prompt: str, system_prompt: str = None, tools: List[Dict] = None, user_id: str = "default", history: List[Dict] = None, **kwargs) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {
                    "Authorization": f"Bearer {self.api_key or 'free'}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://sos.mumega.com",
                    "X-Title": "Sovereign OS"
                }
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.7
                    }
                )

                if resp.status_code != 200:
                    error_msg = resp.text
                    log.error(f"OpenRouter error: {resp.status_code} - {error_msg}")
                    return f"OpenRouter Error: {resp.status_code}"

                data = resp.json()
                return data["choices"][0]["message"]["content"]

        except Exception as e:
            log.error(f"OpenRouter generation failed: {e}")
            return f"OpenRouter Error: {str(e)}"


# Backward compatibility alias
MLXAdapter = LocalAdapter


class LocalCodeAdapter(LocalAdapter):
    """
    Specialized local adapter for coding tasks.
    Works with any loaded coding model (Devstral, CodeLlama, DeepSeek Coder, etc.)
    Optimized prompts for code generation, review, and debugging.
    """

    def __init__(self, server_url: str = None, model_id: str = None):
        super().__init__(
            model_id=model_id or "default",  # Use whatever model is loaded
            server_url=server_url,
            timeout=180.0  # Longer timeout for code generation
        )

    def get_model_id(self) -> str:
        return "local-code"

    async def generate(
        self,
        prompt: str,
        system_prompt: str = None,
        tools: List[Dict] = None,
        user_id: str = "default",
        history: List[Dict] = None,
        **kwargs
    ) -> str:
        # Inject coding-optimized system prompt
        code_system = system_prompt or ""
        code_system = f"""You are a sovereign coding assistant running locally.
You excel at: code generation, debugging, refactoring, and multi-file edits.
Be concise. Output code directly without excessive explanation.
{code_system}"""

        return await super().generate(prompt, code_system, tools)


class LocalReasoningAdapter(LocalAdapter):
    """
    Specialized local adapter for reasoning tasks.
    Works with any loaded reasoning model (Qwen, Llama, Mistral, etc.)
    Supports thinking mode for step-by-step reasoning.
    """

    def __init__(self, server_url: str = None, model_id: str = None):
        super().__init__(
            model_id=model_id or "default",
            server_url=server_url,
            timeout=180.0
        )

    def get_model_id(self) -> str:
        return "local-reasoning"

    async def generate(
        self,
        prompt: str,
        system_prompt: str = None,
        tools: List[Dict] = None,
        user_id: str = "default",
        history: List[Dict] = None,
        **kwargs
    ) -> str:
        # Trigger thinking mode
        reasoning_prompt = f"{prompt}\n\nThink step by step."
        return await super().generate(reasoning_prompt, system_prompt, tools, user_id, history, **kwargs)


# Backward compatibility aliases
MLXCodeAdapter = LocalCodeAdapter
MLXReasoningAdapter = LocalReasoningAdapter
