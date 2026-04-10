"""TROP Video Pipeline — Ephemeris → Script → Voice → Visuals → Video → Post.

This is a TOOL that agents (Sol, Worker) execute via the squad.
Kasra builds it. Sol directs it. The squad runs it daily.

Pipeline stages:
    1. DATA    — ephemeris gives today's cosmic weather (free, deterministic)
    2. SCRIPT  — Sol writes a 15-30s TikTok script (or template fallback)
    3. VOICE   — ElevenLabs TTS renders Sol's voice (or skip for text-only)
    4. VISUALS — Flux generates sacred geometry background (or card fallback)
    5. COMPOSE — Remotion renders final video (or image+caption fallback)
    6. DELIVER — outputs to /cards/ directory for posting agent to pick up

Each stage has a fallback so the pipeline ALWAYS produces something:
    Full pipeline: video with voiceover + AI visuals ($0.05-0.10/run)
    Degraded: static card + caption ($0/run)

Usage:
    from sos.squads.trop.video_pipeline import run_pipeline
    result = run_pipeline()  # Full auto
    result = run_pipeline(sign="Aries")  # Sign-specific
    result = run_pipeline(voice=False)  # Skip voiceover
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sos.squads.trop.video")

OUTPUT_DIR = Path.home() / ".sos" / "trop" / "videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CARDS_DIR = Path.home() / ".sos" / "trop" / "cards"
CARDS_DIR.mkdir(parents=True, exist_ok=True)

# API keys (from env, never hardcoded)
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # "Adam" default
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")

# TikTok script templates (deterministic fallback when Sol is unavailable)
TIKTOK_TEMPLATES = {
    "daily": (
        "Right now, the Moon is in {moon_sign}.\n"
        "That means {moon_energy}\n"
        "The Sun in {sun_sign} adds {sun_energy}\n"
        "Today's dominant pattern is {dominant}.\n"
        "{guidance}\n"
        "Follow for daily cosmic psychology."
    ),
    "sign": (
        "{sign}, listen.\n"
        "With the Moon in {moon_sign} and {phase},\n"
        "{energy}\n"
        "This isn't a horoscope. This is depth psychology\n"
        "through the lens of the cosmos.\n"
        "Follow The Realm of Patterns."
    ),
}


# ── Stage 1: DATA ───────────────────────────────────────────────────────────

def stage_data(sign: str | None = None) -> dict[str, Any]:
    """Get today's cosmic data from ephemeris. Always works. $0."""
    from sos.squads.trop.ephemeris import get_today, SIGN_ENERGY, DIM_GUIDANCE

    data = get_today()
    dominant_dim = data["dominant"]["name"]

    # Get the dominant dimension's guidance
    dim_key = None
    dim_names_map = {"Identity": "P", "Structure": "E", "Mind": "μ", "Heart": "V",
                     "Growth": "N", "Drive": "Δ", "Connection": "R", "Awareness": "Φ"}
    for name, key in dim_names_map.items():
        if name.lower() in dominant_dim.lower():
            dim_key = key
            break

    guidance = ""
    if dim_key:
        state = data["dim_states"].get(dim_key, "high")
        guidance = DIM_GUIDANCE.get(dim_key, {}).get(state, "")

    return {
        "date": data["date"],
        "sun_sign": data["sun_sign"],
        "moon_sign": data["moon_sign"],
        "moon_phase": data["moon_phase"]["name"],
        "phase_meaning": data["moon_phase"]["meaning"],
        "dominant": dominant_dim,
        "signature": data["signature"],
        "sun_energy": SIGN_ENERGY[data["sun_sign"]].split("—")[0].strip(),
        "moon_energy": SIGN_ENERGY[data["moon_sign"]].split("—")[0].strip(),
        "guidance": guidance,
        "sign": sign,
        "vector": data["vector"],
        "dim_states": data["dim_states"],
    }


# ── Stage 2: SCRIPT ─────────────────────────────────────────────────────────

def stage_script(data: dict[str, Any], sol_script: str | None = None) -> str:
    """Generate TikTok script. Sol writes it, or template fallback.

    If sol_script is provided (Sol wrote it), use that.
    Otherwise, fill the template from ephemeris data.
    """
    if sol_script:
        logger.info("Using Sol's custom script (%d chars)", len(sol_script))
        return sol_script

    template_key = "sign" if data.get("sign") else "daily"
    template = TIKTOK_TEMPLATES[template_key]

    script = template.format(
        moon_sign=data["moon_sign"],
        sun_sign=data["sun_sign"],
        moon_energy=data["moon_energy"],
        sun_energy=data["sun_energy"],
        dominant=data["dominant"],
        guidance=data["guidance"],
        phase=data["moon_phase"],
        sign=data.get("sign", ""),
        energy=data.get("moon_energy", ""),
    )

    logger.info("Using template script (%d chars)", len(script))
    return script


# ── Stage 3: VOICE ──────────────────────────────────────────────────────────

def stage_voice(script: str, output_path: Path | None = None) -> Path | None:
    """Render script to audio via ElevenLabs. Returns path or None if skipped.

    Cost: ~$0.01-0.03 per script (depends on length).
    """
    if not ELEVENLABS_API_KEY:
        logger.info("No ElevenLabs key — skipping voice generation")
        return None

    import requests

    if output_path is None:
        output_path = OUTPUT_DIR / f"voice-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.mp3"

    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": script,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {
                    "stability": 0.6,
                    "similarity_boost": 0.8,
                    "style": 0.3,
                },
            },
            timeout=30,
        )
        if resp.status_code == 200:
            output_path.write_bytes(resp.content)
            logger.info("Voice generated: %s (%d bytes)", output_path.name, len(resp.content))
            return output_path
        logger.warning("ElevenLabs failed: %d %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("ElevenLabs error: %s", exc)

    return None


# ── Stage 4: VISUALS ────────────────────────────────────────────────────────

def stage_visuals(data: dict[str, Any], output_path: Path | None = None) -> Path | None:
    """Generate sacred geometry background via Flux on Replicate.

    Cost: ~$0.003/image. Falls back to card generator if unavailable.
    """
    if not REPLICATE_API_TOKEN:
        logger.info("No Replicate token — falling back to card generator")
        return _fallback_card(data)

    import requests

    if output_path is None:
        output_path = OUTPUT_DIR / f"bg-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.png"

    prompt = (
        f"Sacred geometry mandala, cosmic, dark void background #0B1020, "
        f"gold geometric lines #C9A227, {data['moon_phase']} moon, "
        f"{data['sun_sign']} zodiac energy, minimalist, "
        f"no text, no words, abstract mathematical patterns, "
        f"deep space, stars, 1080x1920 vertical"
    )

    try:
        # Start prediction
        resp = requests.post(
            "https://api.replicate.com/v1/predictions",
            headers={
                "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "version": "black-forest-labs/flux-schnell",
                "input": {
                    "prompt": prompt,
                    "num_outputs": 1,
                    "aspect_ratio": "9:16",
                    "output_format": "png",
                },
            },
            timeout=30,
        )

        if resp.status_code in (200, 201):
            prediction = resp.json()
            pred_url = prediction.get("urls", {}).get("get", "")

            # Poll for completion (max 60s)
            import time
            for _ in range(30):
                time.sleep(2)
                poll = requests.get(
                    pred_url,
                    headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"},
                    timeout=10,
                )
                result = poll.json()
                if result.get("status") == "succeeded":
                    image_url = result["output"][0]
                    img_resp = requests.get(image_url, timeout=30)
                    output_path.write_bytes(img_resp.content)
                    logger.info("Visual generated: %s", output_path.name)
                    return output_path
                if result.get("status") == "failed":
                    break

        logger.warning("Replicate failed, falling back to card")
    except Exception as exc:
        logger.warning("Replicate error: %s, falling back to card", exc)

    return _fallback_card(data)


def _fallback_card(data: dict[str, Any]) -> Path | None:
    """Generate a static card as visual fallback."""
    try:
        from sos.squads.trop.cards import generate_daily_card
        from sos.squads.trop.ephemeris import get_today
        full_data = get_today()
        return generate_daily_card(full_data)
    except Exception as exc:
        logger.warning("Card fallback also failed: %s", exc)
        return None


# ── Stage 5: COMPOSE ────────────────────────────────────────────────────────

def stage_compose(
    script: str,
    voice_path: Path | None,
    visual_path: Path | None,
    data: dict[str, Any],
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Compose final output. Video if we have voice+visual, image+caption otherwise.

    Remotion composition when available, ffmpeg fallback, or just image+text.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

    if output_path is None:
        output_path = OUTPUT_DIR / f"trop-{date_str}.mp4"

    # Best case: we have voice + visual → make video with ffmpeg
    if voice_path and visual_path:
        video_path = _compose_ffmpeg(visual_path, voice_path, output_path)
        if video_path:
            return {
                "type": "video",
                "path": str(video_path),
                "caption": _build_caption(data),
                "script": script,
            }

    # Degraded: just image + caption
    if visual_path:
        return {
            "type": "image",
            "path": str(visual_path),
            "caption": _build_caption(data, include_script=True),
            "script": script,
        }

    # Minimum: text only
    return {
        "type": "text",
        "path": None,
        "caption": _build_caption(data, include_script=True),
        "script": script,
    }


def _compose_ffmpeg(image_path: Path, audio_path: Path, output_path: Path) -> Path | None:
    """Create video from static image + audio using ffmpeg."""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(image_path),
            "-i", str(audio_path),
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-shortest",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode == 0:
            logger.info("Video composed: %s", output_path.name)
            return output_path
        logger.warning("ffmpeg failed: %s", result.stderr.decode()[:200])
    except FileNotFoundError:
        logger.warning("ffmpeg not installed — skipping video composition")
    except Exception as exc:
        logger.warning("Compose error: %s", exc)

    return None


def _build_caption(data: dict[str, Any], include_script: bool = False) -> str:
    """Build social media caption from cosmic data."""
    lines = [
        f"{data['moon_phase']} · Moon in {data['moon_sign']}",
        f"Sun in {data['sun_sign']} — {data['sun_energy']}",
        f"Today's pattern: {data['dominant']}",
        "",
    ]
    if include_script:
        lines.append(data.get("guidance", ""))
        lines.append("")

    lines.extend([
        "therealmofpatterns.com",
        "",
        "#astrology #cosmicweather #jungianpsychology #depthpsychology "
        "#zodiac #therealmofpatterns #spiritualtiktok #dailyreading "
        f"#{data['sun_sign'].lower()} #{data['moon_sign'].lower()}",
    ])
    return "\n".join(lines)


# ── Stage 6: DELIVER ────────────────────────────────────────────────────────

def stage_deliver(result: dict[str, Any]) -> dict[str, Any]:
    """Deliver output to pickup directory + write manifest for posting agent.

    The posting agent (Worker/Sol) reads manifests and posts to platforms.
    This stage does NOT post directly — that's the posting agent's job.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    manifest_path = OUTPUT_DIR / f"manifest-{date_str}.json"

    manifest = {
        "created": datetime.now(timezone.utc).isoformat(),
        "type": result["type"],
        "media_path": result.get("path"),
        "caption": result["caption"],
        "script": result["script"],
        "platforms": ["tiktok", "instagram_reels", "youtube_shorts"],
        "status": "ready",
        "posted": {},
    }

    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.info("Manifest written: %s", manifest_path.name)

    return {
        "manifest": str(manifest_path),
        **result,
    }


# ── FULL PIPELINE ───────────────────────────────────────────────────────────

def run_pipeline(
    sign: str | None = None,
    sol_script: str | None = None,
    voice: bool = True,
    visuals: bool = True,
) -> dict[str, Any]:
    """Run the full TROP video pipeline.

    Args:
        sign: Specific zodiac sign (None = daily general)
        sol_script: Custom script from Sol (None = use template)
        voice: Generate voiceover (requires ELEVENLABS_API_KEY)
        visuals: Generate AI background (requires REPLICATE_API_TOKEN)

    Returns:
        Pipeline result with type (video/image/text), paths, caption.
    """
    logger.info("TROP video pipeline starting (sign=%s, voice=%s, visuals=%s)", sign, voice, visuals)

    # Stage 1: Data
    data = stage_data(sign=sign)
    logger.info("DATA: %s Sun, %s Moon, %s, dominant=%s",
                data["sun_sign"], data["moon_sign"], data["moon_phase"], data["dominant"])

    # Stage 2: Script
    script = stage_script(data, sol_script=sol_script)
    logger.info("SCRIPT: %d chars", len(script))

    # Stage 3: Voice (optional)
    voice_path = None
    if voice:
        voice_path = stage_voice(script)
        logger.info("VOICE: %s", voice_path or "skipped")

    # Stage 4: Visuals (optional)
    visual_path = None
    if visuals:
        visual_path = stage_visuals(data)
        logger.info("VISUALS: %s", visual_path or "skipped")

    # Stage 5: Compose
    composed = stage_compose(script, voice_path, visual_path, data)
    logger.info("COMPOSE: type=%s", composed["type"])

    # Stage 6: Deliver
    result = stage_deliver(composed)
    logger.info("DELIVER: manifest at %s", result["manifest"])

    return result


# ── BATCH: All 12 signs ─────────────────────────────────────────────────────

def run_batch_signs(voice: bool = False, visuals: bool = True) -> list[dict[str, Any]]:
    """Generate content for all 12 signs. Great for batch posting."""
    signs = [
        "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
        "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
    ]
    results = []
    for sign in signs:
        result = run_pipeline(sign=sign, voice=voice, visuals=visuals)
        results.append(result)
        logger.info("Batch: %s → %s", sign, result["type"])
    return results
