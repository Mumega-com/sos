
import logging
import hashlib
from typing import Optional, List, Dict, Any
from pathlib import Path
from google import genai
from google.genai import types

logger = logging.getLogger("sos.gemini_cache")

class GeminiCacheManager:
    """
    Implements the 'Good' caching strategy from resident-cms.
    Uses Google's Context Caching API to pin FRC context and history.
    """
    def __init__(self, client: genai.Client):
        self.client = client
        self._user_caches: Dict[str, str] = {} # user_id_hash -> cache_name

    def _generate_cache_key(self, user_id: str, model: str, system_prompt: str, history: List[Dict], tools: List[Any] = None) -> str:
        """
        Hashes the context to determine if a cache can be reused.
        """
        # Simplify history for hashing
        history_summary = []
        for msg in history[-10:]: # Hash last 10 turns
            history_summary.append((msg.get('role'), len(msg.get('content', ''))))
        
        hasher = hashlib.md5()
        hasher.update(user_id.encode())
        hasher.update(model.encode())
        hasher.update(system_prompt.encode())
        hasher.update(str(history_summary).encode())
        if tools:
            hasher.update(str(tools).encode())
        
        return hasher.hexdigest()

    async def get_or_create_cache(self, user_id: str, model: str, system_prompt: str, history: List[Dict], tools: List[Any] = None, base_context: str = None) -> Optional[str]:
        """
        Creates a server-side context cache.
        Supports a 'Base Context' (up to 5M tokens) representing the foundation exploration.
        """
        cache_key = self._generate_cache_key(user_id, model, system_prompt, history, tools)
        if base_context:
            cache_key += hashlib.md5(base_context.encode()).hexdigest()[:8]
        
        if cache_key in self._user_caches:
            return self._user_caches[cache_key]

        try:
            # Prepare contents
            contents = []
            
            # 1. Inject Base Context (The 5M Foundation)
            if base_context:
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text=f"BASE FOUNDATION:\n{base_context}")]
                ))
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part(text="Base foundation accepted. My curvature is now anchored in this history.")]
                ))

            # 2. Inject Active History
            for msg in history:
                role = "model" if msg['role'] == "assistant" else "user"
                contents.append(types.Content(
                    role=role,
                    parts=[types.Part(text=msg['content'])]
                ))

            actual_model = model if model.startswith("models/") else f"models/{model}"

            cache_config = types.CreateCachedContentConfig(
                system_instruction=types.Content(
                    role="system",
                    parts=[types.Part(text=system_prompt)]
                ),
                display_name=f"sos_cache_{user_id[:8]}",
                ttl="3600s",
                contents=contents if contents else None,
                tools=tools if tools else None
            )

            cache = self.client.caches.create(
                model=actual_model,
                config=cache_config
            )

            cache_name = cache.name
            self._user_caches[cache_key] = cache_name
            logger.info(f"📦 Created Gemini Cache: {cache_name} (Key: {cache_key[:8]})")
            return cache_name

        except Exception as e:
            msg = str(e).lower()
            if "too small" in msg:
                logger.debug("Context too small for server-side caching (<1024 tokens).")
            else:
                logger.warning(f"Failed to create Gemini cache: {e}")
            return None
