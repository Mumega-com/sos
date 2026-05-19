"""
Voice Synthesis Core - Multi-provider TTS system for SOS

Supports:
- ElevenLabs (default) - High quality, emotional voices
- OpenAI TTS - Fast, clear voices
- Gemini TTS - Native multimodal

Adapted from /cli/mumega/core/voice.py for SOS architecture.

Partner Integration:
- Profiles can be loaded from dnu_partners table via partner_id
- heygen_voice_id maps to custom ElevenLabs voice
"""

import os
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, AsyncIterator, List
from dataclasses import dataclass, field
from enum import Enum
import io

logger = logging.getLogger(__name__)

# Supabase client for partner data
_supabase_client = None

def get_supabase():
    """Get or create Supabase client for partner lookups"""
    global _supabase_client
    if _supabase_client is None:
        url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        if url and key:
            try:
                from supabase import create_client
                _supabase_client = create_client(url, key)
                logger.info("Supabase client initialized for voice service")
            except ImportError:
                logger.warning("supabase package not installed - partner profiles disabled")
            except Exception as e:
                logger.warning(f"Failed to init Supabase: {e}")
    return _supabase_client

# Audio format constants
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


class Provider(str, Enum):
    """Supported voice providers"""
    ELEVENLABS = "elevenlabs"
    OPENAI = "openai"
    GEMINI = "gemini"


@dataclass
class VoiceConfig:
    """Configuration for voice synthesis"""
    voice_id: str = "dandan"
    model: str = "turbo"
    stability: float = 0.5
    similarity_boost: float = 0.75
    speed: float = 1.0
    output_format: str = "mp3"


@dataclass
class AudioChunk:
    """A chunk of audio data for streaming"""
    data: bytes
    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    format: str = "mp3"


@dataclass
class VoiceProfile:
    """Voice profile for a practice/agent"""
    profile_id: str
    provider: Provider = Provider.ELEVENLABS
    voice_id: str = "dandan"
    name: str = "Dandan"
    description: str = "Warm, helpful dental assistant"
    config: VoiceConfig = field(default_factory=VoiceConfig)

    # Custom voice (cloned)
    is_custom: bool = False
    custom_voice_id: Optional[str] = None  # ElevenLabs cloned voice ID


class VoiceProvider(ABC):
    """Abstract base class for voice providers"""

    @abstractmethod
    async def synthesize(self, text: str, config: VoiceConfig) -> bytes:
        """Synthesize text to audio bytes"""
        pass

    @abstractmethod
    async def stream(self, text: str, config: VoiceConfig) -> AsyncIterator[AudioChunk]:
        """Stream audio chunks as they're generated"""
        pass

    @abstractmethod
    def get_voices(self) -> List[Dict[str, str]]:
        """List available voices"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name"""
        pass


class ElevenLabsProvider(VoiceProvider):
    """ElevenLabs TTS - High quality, emotional voices"""

    # Voice presets for Dandan
    # ElevenLabs Voice IDs - verified 2026-01-18
    # https://elevenlabs.io/docs/api-reference/voices/search
    VOICES = {
        # Dandan voices (dental assistant)
        "dandan": "EXAVITQu4vr4xnSDxMaL",          # Sarah - Mature, Reassuring (default)
        "dandan_friendly": "21m00Tcm4TlvDq8ikWAM", # Rachel - Conversational
        "dandan_calm": "9BWtsMINqrJLrRacOk9x",     # Aria - Informative, Educational
        "dandan_warm": "cgSgspJ2msm6clMCkdW9",     # Jessica - Playful, Bright
        "dandan_gentle": "FGY2WhTYpPnrIDTdsKH5",   # Laura - Enthusiast

        # Alternative female voices
        "alice": "Xb7hH8MSUJpSbSDYk0k2",           # Alice - Clear, Engaging (British)
        "lily": "pFZP5JQG7iQjIQuC4Bku",            # Lily - Velvety (British)
        "matilda": "XrExE9yKIg1WjnnlVkGX",         # Matilda - Knowledgeable
        "charlotte": "XB0fDUnXU5powFXDhCwa",       # Charlotte - Clear

        # Neutral/Other
        "river": "SAz9YHcvj6GT2YYXdXww",           # River - Relaxed, Neutral

        # Male voices (for dentist persona if needed)
        "george": "JBFqnCBsd6RMkjVDRZzb",          # George - Warm (British)
        "brian": "nPczCjzI2devNBz1zQrb",           # Brian - Deep, Reassuring
        "daniel": "onwK4e9ZLuTAKqWW03F9",          # Daniel - Steady (British)
    }

    MODELS = {
        "multilingual": "eleven_multilingual_v2",
        "turbo": "eleven_turbo_v2_5",
        "flash": "eleven_flash_v2_5",
    }

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        self.client = None
        self._init_client()

    def _init_client(self):
        if self.api_key:
            try:
                from elevenlabs.client import ElevenLabs
                self.client = ElevenLabs(api_key=self.api_key)
                logger.info("ElevenLabs voice provider initialized")
            except ImportError:
                logger.warning("elevenlabs package not installed")

    @property
    def name(self) -> str:
        return "elevenlabs"

    def get_voices(self) -> List[Dict[str, str]]:
        return [{"id": k, "name": k, "provider_id": v} for k, v in self.VOICES.items()]

    async def synthesize(self, text: str, config: VoiceConfig) -> bytes:
        if not self.client:
            raise RuntimeError("ElevenLabs client not initialized")

        voice_id = self.VOICES.get(config.voice_id, config.voice_id)
        model_id = self.MODELS.get(config.model, self.MODELS["turbo"])

        try:
            audio = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=text,
                    model_id=model_id,
                    voice_settings={
                        "stability": config.stability,
                        "similarity_boost": config.similarity_boost
                    }
                )
            )

            audio_bytes = b"".join(audio)
            logger.debug(f"ElevenLabs synthesized {len(audio_bytes)} bytes")
            return audio_bytes

        except Exception as e:
            logger.error(f"ElevenLabs synthesis error: {e}")
            raise

    async def stream(self, text: str, config: VoiceConfig) -> AsyncIterator[AudioChunk]:
        if not self.client:
            raise RuntimeError("ElevenLabs client not initialized")

        voice_id = self.VOICES.get(config.voice_id, config.voice_id)
        model_id = self.MODELS.get(config.model, self.MODELS["turbo"])

        try:
            audio_stream = self.client.text_to_speech.convert_as_stream(
                voice_id=voice_id,
                text=text,
                model_id=model_id,
            )

            for chunk in audio_stream:
                yield AudioChunk(data=chunk, format="mp3")

        except Exception as e:
            logger.error(f"ElevenLabs stream error: {e}")
            raise

    async def clone_voice(self, name: str, audio_files: List[bytes], description: str = None) -> str:
        """Clone a voice from audio samples (for custom practice voices)"""
        if not self.client:
            raise RuntimeError("ElevenLabs client not initialized")

        try:
            voice = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.clone(
                    name=name,
                    description=description or f"Custom voice for {name}",
                    files=audio_files
                )
            )
            logger.info(f"Cloned voice '{name}' with ID: {voice.voice_id}")
            return voice.voice_id
        except Exception as e:
            logger.error(f"Voice cloning error: {e}")
            raise


class OpenAIProvider(VoiceProvider):
    """OpenAI TTS - Fast, clear voices"""

    VOICES = {
        "dandan": "nova",      # Female, warm
        "alloy": "alloy",
        "echo": "echo",
        "fable": "fable",
        "onyx": "onyx",
        "nova": "nova",
        "shimmer": "shimmer",
    }

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.client = None
        self._init_client()

    def _init_client(self):
        if self.api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=self.api_key)
                logger.info("OpenAI voice provider initialized")
            except ImportError:
                logger.warning("openai package not installed")

    @property
    def name(self) -> str:
        return "openai"

    def get_voices(self) -> List[Dict[str, str]]:
        return [{"id": k, "name": k, "provider_id": v} for k, v in self.VOICES.items()]

    async def synthesize(self, text: str, config: VoiceConfig) -> bytes:
        if not self.client:
            raise RuntimeError("OpenAI client not initialized")

        voice = self.VOICES.get(config.voice_id, "nova")

        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.audio.speech.create(
                    model="tts-1",
                    voice=voice,
                    input=text,
                    speed=config.speed,
                    response_format="mp3"
                )
            )

            return response.content

        except Exception as e:
            logger.error(f"OpenAI synthesis error: {e}")
            raise

    async def stream(self, text: str, config: VoiceConfig) -> AsyncIterator[AudioChunk]:
        if not self.client:
            raise RuntimeError("OpenAI client not initialized")

        voice = self.VOICES.get(config.voice_id, "nova")

        try:
            with self.client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice=voice,
                input=text,
                speed=config.speed,
                response_format="mp3"
            ) as response:
                for chunk in response.iter_bytes(chunk_size=4096):
                    yield AudioChunk(data=chunk, format="mp3")

        except Exception as e:
            logger.error(f"OpenAI stream error: {e}")
            raise


class GeminiProvider(VoiceProvider):
    """Google Gemini TTS - Native multimodal"""

    VOICES = {
        "dandan": "Kore",
        "kore": "Kore",
        "puck": "Puck",
        "charon": "Charon",
        "aoede": "Aoede",
    }

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.client = None
        self._init_client()

    def _init_client(self):
        if self.api_key:
            try:
                from google import genai
                use_vertex = os.getenv("SOS_USE_VERTEX_AI", "false").lower() in ("true", "1", "t", "y", "yes")
                self.client = genai.Client(api_key=self.api_key, vertexai=use_vertex)
                logger.info(f"Gemini voice provider initialized (VertexAI: {use_vertex})")
            except ImportError:
                logger.warning("google-genai package not installed")

    @property
    def name(self) -> str:
        return "gemini"

    def get_voices(self) -> List[Dict[str, str]]:
        return [{"id": k, "name": k, "provider_id": v} for k, v in self.VOICES.items()]

    async def synthesize(self, text: str, config: VoiceConfig) -> bytes:
        if not self.client:
            raise RuntimeError("Gemini client not initialized")

        voice_name = self.VOICES.get(config.voice_id, "Kore")

        try:
            from google.genai import types

            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.models.generate_content(
                    model="gemini-2.5-flash-preview-tts",
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=voice_name,
                                )
                            )
                        )
                    )
                )
            )

            return response.candidates[0].content.parts[0].inline_data.data

        except Exception as e:
            logger.error(f"Gemini synthesis error: {e}")
            raise

    async def stream(self, text: str, config: VoiceConfig) -> AsyncIterator[AudioChunk]:
        # Gemini doesn't support streaming TTS yet
        audio = await self.synthesize(text, config)
        yield AudioChunk(data=audio, format="wav")


class VoiceService:
    """
    Main voice synthesis service for SOS.

    Usage:
        service = VoiceService()
        audio = await service.synthesize("Hello from Dandan!")

        # With profile
        profile = VoiceProfile(profile_id="clinic_123", voice_id="dandan")
        audio = await service.synthesize("Welcome!", profile=profile)

        # Streaming
        async for chunk in service.stream("Long text..."):
            send_to_client(chunk.data)
    """

    PROVIDERS = {
        Provider.ELEVENLABS: ElevenLabsProvider,
        Provider.OPENAI: OpenAIProvider,
        Provider.GEMINI: GeminiProvider,
    }

    def __init__(self, default_provider: Provider = None):
        self.providers: Dict[Provider, VoiceProvider] = {}
        self.default_provider = default_provider or self._auto_select_provider()
        self._init_providers()

        # Voice profiles cache (practice_id -> profile)
        self.profiles: Dict[str, VoiceProfile] = {}

    def _auto_select_provider(self) -> Provider:
        """Auto-select provider based on available API keys"""
        if os.getenv("ELEVENLABS_API_KEY"):
            return Provider.ELEVENLABS
        elif os.getenv("OPENAI_API_KEY"):
            return Provider.OPENAI
        elif os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            return Provider.GEMINI
        return Provider.ELEVENLABS  # Default

    def _init_providers(self):
        """Initialize all available providers"""
        for provider_enum, provider_class in self.PROVIDERS.items():
            try:
                provider = provider_class()
                if hasattr(provider, 'client') and provider.client:
                    self.providers[provider_enum] = provider
                    logger.info(f"Initialized {provider_enum.value} provider")
            except Exception as e:
                logger.warning(f"Failed to initialize {provider_enum.value}: {e}")

    @property
    def is_available(self) -> bool:
        return len(self.providers) > 0

    def get_provider(self, provider: Provider = None) -> VoiceProvider:
        """Get a specific provider or the default"""
        provider = provider or self.default_provider
        if provider not in self.providers:
            raise RuntimeError(f"Provider {provider.value} not available")
        return self.providers[provider]

    def register_profile(self, profile: VoiceProfile):
        """Register a voice profile for a practice"""
        self.profiles[profile.profile_id] = profile
        logger.info(f"Registered voice profile: {profile.profile_id}")

    def get_profile(self, profile_id: str) -> Optional[VoiceProfile]:
        """
        Get a voice profile by ID.

        Lookup order:
        1. In-memory cache (for manually registered profiles)
        2. dnu_partners table (for partner_id lookups)

        Partner data is cached after first lookup.
        """
        # Check cache first
        if profile_id in self.profiles:
            return self.profiles[profile_id]

        # Try to load from partner table
        profile = self._load_partner_profile(profile_id)
        if profile:
            self.profiles[profile_id] = profile
            logger.info(f"Loaded partner profile from Supabase: {profile_id}")

        return profile

    def _load_partner_profile(self, partner_id: str) -> Optional[VoiceProfile]:
        """Load voice profile from dnu_partners table"""
        supabase = get_supabase()
        if not supabase:
            return None

        try:
            result = supabase.table("dnu_partners").select(
                "id, practice_name, heygen_voice_id"
            ).eq("id", partner_id).single().execute()

            if result.data:
                partner = result.data
                voice_id = partner.get("heygen_voice_id")

                # If partner has custom voice, use it; otherwise use dandan default
                return VoiceProfile(
                    profile_id=partner["id"],
                    provider=Provider.ELEVENLABS,
                    voice_id="dandan",  # Default
                    name=partner.get("practice_name", "Practice"),
                    description=f"Voice profile for {partner.get('practice_name', 'partner')}",
                    is_custom=bool(voice_id),
                    custom_voice_id=voice_id,  # ElevenLabs custom voice ID
                )
        except Exception as e:
            logger.warning(f"Failed to load partner profile {partner_id}: {e}")

        return None

    async def synthesize(
        self,
        text: str,
        voice_id: str = "dandan",
        profile: VoiceProfile = None,
        provider: Provider = None,
        config: VoiceConfig = None
    ) -> bytes:
        """
        Synthesize text to audio.

        Args:
            text: Text to synthesize
            voice_id: Voice ID (overridden by profile if provided)
            profile: Voice profile for a specific practice
            provider: Specific provider to use
            config: Voice configuration

        Returns:
            Audio bytes (MP3 format)
        """
        if profile:
            voice_id = profile.custom_voice_id or profile.voice_id
            provider = provider or profile.provider
            config = config or profile.config

        config = config or VoiceConfig(voice_id=voice_id)
        config.voice_id = voice_id

        voice_provider = self.get_provider(provider)

        logger.info(f"Synthesizing with {voice_provider.name}: '{text[:50]}...'")
        return await voice_provider.synthesize(text, config)

    async def stream(
        self,
        text: str,
        voice_id: str = "dandan",
        profile: VoiceProfile = None,
        provider: Provider = None,
        config: VoiceConfig = None
    ) -> AsyncIterator[AudioChunk]:
        """Stream audio chunks"""
        if profile:
            voice_id = profile.custom_voice_id or profile.voice_id
            provider = provider or profile.provider
            config = config or profile.config

        config = config or VoiceConfig(voice_id=voice_id)
        config.voice_id = voice_id

        voice_provider = self.get_provider(provider)

        async for chunk in voice_provider.stream(text, config):
            yield chunk

    async def clone_voice(
        self,
        name: str,
        audio_files: List[bytes],
        description: str = None
    ) -> str:
        """
        Clone a custom voice (ElevenLabs only).

        Args:
            name: Name for the cloned voice
            audio_files: List of audio samples (bytes)
            description: Description of the voice

        Returns:
            Voice ID for the cloned voice
        """
        provider = self.get_provider(Provider.ELEVENLABS)
        if not isinstance(provider, ElevenLabsProvider):
            raise RuntimeError("Voice cloning only available with ElevenLabs")

        return await provider.clone_voice(name, audio_files, description)


# Singleton instance
_voice_service: Optional[VoiceService] = None


def get_voice_service() -> VoiceService:
    """Get or create the voice service singleton"""
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service
