"""
SOS Voice Service - Multi-provider TTS for Dandan

Provides voice synthesis capabilities for the Dandan agent:
- ElevenLabs (default, high quality)
- OpenAI TTS
- Google Gemini TTS

Usage:
    from sos.services.voice import VoiceService
    voice = VoiceService()
    audio = await voice.synthesize("Hello, I am Dandan", voice_id="dandan")
"""

from .core import VoiceService, VoiceConfig, VoiceProvider
from .app import create_app

__all__ = ["VoiceService", "VoiceConfig", "VoiceProvider", "create_app"]
