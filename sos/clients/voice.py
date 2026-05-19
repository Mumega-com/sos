"""
Voice Service Client for SOS

Usage:
    from sos.clients.voice import VoiceClient

    client = VoiceClient()
    audio = await client.synthesize("Hello from Dandan!")

    # With profile
    audio = await client.synthesize("Welcome!", profile_id="clinic_123")

    # Stream
    async for chunk in client.stream("Long text..."):
        send_to_websocket(chunk)
"""

import os
import logging
from typing import Optional, AsyncIterator, Dict, Any
import httpx

logger = logging.getLogger(__name__)


class VoiceClient:
    """Client for the SOS Voice Service"""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or os.getenv("SOS_VOICE_URL", "http://localhost:6065")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=60.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health(self) -> Dict[str, Any]:
        """Check voice service health"""
        client = await self._get_client()
        response = await client.get("/health")
        response.raise_for_status()
        return response.json()

    async def list_voices(self, provider: str = None) -> list:
        """List available voices"""
        client = await self._get_client()
        params = {"provider": provider} if provider else {}
        response = await client.get("/voices", params=params)
        response.raise_for_status()
        return response.json()

    async def synthesize(
        self,
        text: str,
        voice_id: str = "dandan",
        profile_id: str = None,
        provider: str = None,
        model: str = "turbo",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        speed: float = 1.0,
    ) -> bytes:
        """
        Synthesize text to audio.

        Args:
            text: Text to synthesize
            voice_id: Voice ID
            profile_id: Practice profile ID (overrides voice_id)
            provider: Provider (elevenlabs, openai, gemini)
            model: Model (turbo, flash, multilingual)
            stability: Voice stability (0-1)
            similarity_boost: Voice similarity (0-1)
            speed: Speech speed (0.5-2.0)

        Returns:
            Audio bytes (MP3)
        """
        client = await self._get_client()

        payload = {
            "text": text,
            "voice_id": voice_id,
            "model": model,
            "stability": stability,
            "similarity_boost": similarity_boost,
            "speed": speed,
        }

        if profile_id:
            payload["profile_id"] = profile_id
        if provider:
            payload["provider"] = provider

        response = await client.post("/synthesize", json=payload)
        response.raise_for_status()

        return response.content

    async def stream(
        self,
        text: str,
        voice_id: str = "dandan",
        provider: str = None,
    ) -> AsyncIterator[bytes]:
        """
        Stream synthesized audio chunks.

        Yields:
            Audio chunks (bytes)
        """
        client = await self._get_client()

        payload = {
            "text": text,
            "voice_id": voice_id,
        }
        if provider:
            payload["provider"] = provider

        async with client.stream("POST", "/stream", json=payload) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    async def register_profile(
        self,
        profile_id: str,
        voice_id: str = "dandan",
        name: str = "Dandan",
        description: str = "Dental assistant",
        provider: str = "elevenlabs",
        custom_voice_id: str = None,
    ) -> Dict[str, Any]:
        """Register a voice profile for a practice"""
        client = await self._get_client()

        payload = {
            "profile_id": profile_id,
            "voice_id": voice_id,
            "name": name,
            "description": description,
            "provider": provider,
        }
        if custom_voice_id:
            payload["custom_voice_id"] = custom_voice_id

        response = await client.post("/profiles", json=payload)
        response.raise_for_status()

        return response.json()

    async def get_profile(self, profile_id: str) -> Dict[str, Any]:
        """Get a voice profile"""
        client = await self._get_client()
        response = await client.get(f"/profiles/{profile_id}")
        response.raise_for_status()
        return response.json()

    async def delete_profile(self, profile_id: str) -> Dict[str, Any]:
        """Delete a voice profile"""
        client = await self._get_client()
        response = await client.delete(f"/profiles/{profile_id}")
        response.raise_for_status()
        return response.json()


# Convenience function for quick synthesis
async def speak(text: str, voice_id: str = "dandan", **kwargs) -> bytes:
    """Quick helper to synthesize text"""
    client = VoiceClient()
    try:
        return await client.synthesize(text, voice_id=voice_id, **kwargs)
    finally:
        await client.close()
