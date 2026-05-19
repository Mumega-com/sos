"""
Voice Service FastAPI Application

Provides HTTP endpoints for voice synthesis:
- POST /synthesize - Generate audio from text
- POST /stream - Stream audio chunks
- POST /clone - Clone a custom voice
- GET /voices - List available voices
- GET /health - Health check

Port: 6065 (default)
"""

import os
import logging
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .core import (
    VoiceService,
    VoiceConfig,
    VoiceProfile,
    Provider,
    get_voice_service,
)

logger = logging.getLogger(__name__)

# Request/Response models


class SynthesizeRequest(BaseModel):
    """Request to synthesize text to audio"""
    text: str = Field(..., description="Text to synthesize", max_length=5000)
    voice_id: str = Field(default="dandan", description="Voice ID")
    provider: Optional[str] = Field(default=None, description="Provider (elevenlabs, openai, gemini)")
    profile_id: Optional[str] = Field(default=None, description="Practice profile ID")

    # Voice settings
    model: str = Field(default="turbo", description="Model (turbo, flash, multilingual)")
    stability: float = Field(default=0.5, ge=0, le=1, description="Stability (0-1)")
    similarity_boost: float = Field(default=0.75, ge=0, le=1, description="Similarity boost (0-1)")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Speed (0.5-2.0)")


class RegisterProfileRequest(BaseModel):
    """Request to register a voice profile"""
    profile_id: str = Field(..., description="Unique profile ID (e.g., practice_123)")
    voice_id: str = Field(default="dandan", description="Default voice ID")
    name: str = Field(default="Dandan", description="Display name")
    description: str = Field(default="Dental assistant", description="Description")
    provider: str = Field(default="elevenlabs", description="Default provider")
    custom_voice_id: Optional[str] = Field(default=None, description="Custom cloned voice ID")


class CloneVoiceRequest(BaseModel):
    """Request to clone a voice"""
    name: str = Field(..., description="Name for the cloned voice")
    description: Optional[str] = Field(default=None, description="Voice description")
    # Audio files sent as base64 or multipart


class VoiceInfo(BaseModel):
    """Voice information"""
    id: str
    name: str
    provider_id: str


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    provider: str
    available_providers: List[str]


def create_app() -> FastAPI:
    """Create the FastAPI application"""

    app = FastAPI(
        title="SOS Voice Service",
        description="Voice synthesis service for Dandan - multi-provider TTS",
        version="1.0.0",
    )

    # Initialize service on startup
    @app.on_event("startup")
    async def startup():
        service = get_voice_service()
        logger.info(f"Voice service started. Default provider: {service.default_provider.value}")
        logger.info(f"Available providers: {[p.value for p in service.providers.keys()]}")

    @app.get("/health", response_model=HealthResponse)
    async def health_check():
        """Check voice service health"""
        service = get_voice_service()
        return HealthResponse(
            status="healthy" if service.is_available else "degraded",
            provider=service.default_provider.value,
            available_providers=[p.value for p in service.providers.keys()]
        )

    @app.get("/voices", response_model=List[VoiceInfo])
    async def list_voices(provider: Optional[str] = None):
        """List available voices"""
        service = get_voice_service()

        if provider:
            try:
                p = Provider(provider)
                voice_provider = service.get_provider(p)
                return voice_provider.get_voices()
            except (ValueError, RuntimeError) as e:
                raise HTTPException(status_code=400, detail=str(e))

        # Return all voices from default provider
        return service.get_provider().get_voices()

    @app.post("/synthesize")
    async def synthesize(request: SynthesizeRequest):
        """
        Synthesize text to audio.

        Returns MP3 audio bytes.
        """
        service = get_voice_service()

        if not service.is_available:
            raise HTTPException(status_code=503, detail="Voice service unavailable")

        try:
            # Get profile if specified
            profile = None
            if request.profile_id:
                profile = service.get_profile(request.profile_id)

            # Build config
            config = VoiceConfig(
                voice_id=request.voice_id,
                model=request.model,
                stability=request.stability,
                similarity_boost=request.similarity_boost,
                speed=request.speed,
            )

            # Parse provider
            provider = None
            if request.provider:
                try:
                    provider = Provider(request.provider)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Unknown provider: {request.provider}")

            # Synthesize
            audio_bytes = await service.synthesize(
                text=request.text,
                voice_id=request.voice_id,
                profile=profile,
                provider=provider,
                config=config,
            )

            return Response(
                content=audio_bytes,
                media_type="audio/mpeg",
                headers={
                    "Content-Disposition": "attachment; filename=speech.mp3",
                    "X-Voice-Provider": service.get_provider(provider).name,
                }
            )

        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/stream")
    async def stream_audio(request: SynthesizeRequest):
        """
        Stream synthesized audio chunks.

        Returns chunked MP3 audio.
        """
        service = get_voice_service()

        if not service.is_available:
            raise HTTPException(status_code=503, detail="Voice service unavailable")

        try:
            config = VoiceConfig(
                voice_id=request.voice_id,
                model=request.model,
                stability=request.stability,
                similarity_boost=request.similarity_boost,
                speed=request.speed,
            )

            provider = None
            if request.provider:
                provider = Provider(request.provider)

            async def generate():
                async for chunk in service.stream(
                    text=request.text,
                    voice_id=request.voice_id,
                    provider=provider,
                    config=config,
                ):
                    yield chunk.data

            return StreamingResponse(
                generate(),
                media_type="audio/mpeg",
                headers={"X-Voice-Provider": service.get_provider(provider).name}
            )

        except Exception as e:
            logger.error(f"Stream error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/profiles")
    async def register_profile(request: RegisterProfileRequest):
        """Register a voice profile for a practice"""
        service = get_voice_service()

        try:
            provider = Provider(request.provider)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {request.provider}")

        profile = VoiceProfile(
            profile_id=request.profile_id,
            provider=provider,
            voice_id=request.voice_id,
            name=request.name,
            description=request.description,
            custom_voice_id=request.custom_voice_id,
            is_custom=request.custom_voice_id is not None,
        )

        service.register_profile(profile)

        return {"status": "registered", "profile_id": profile.profile_id}

    @app.get("/profiles/{profile_id}")
    async def get_profile(profile_id: str):
        """Get a registered voice profile"""
        service = get_voice_service()
        profile = service.get_profile(profile_id)

        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        return {
            "profile_id": profile.profile_id,
            "name": profile.name,
            "voice_id": profile.voice_id,
            "provider": profile.provider.value,
            "is_custom": profile.is_custom,
        }

    @app.delete("/profiles/{profile_id}")
    async def delete_profile(profile_id: str):
        """Delete a voice profile"""
        service = get_voice_service()

        if profile_id in service.profiles:
            del service.profiles[profile_id]
            return {"status": "deleted", "profile_id": profile_id}

        raise HTTPException(status_code=404, detail="Profile not found")

    return app


# Create default app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("VOICE_SERVICE_PORT", "6065"))
    uvicorn.run(app, host="0.0.0.0", port=port)
