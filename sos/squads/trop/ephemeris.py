"""TROP Ephemeris — deterministic content from planetary positions.

No LLM needed. Templates + real ephemeris data = daily cosmic weather.
LLM is the seasoning, not the food.

Usage:
    from sos.squads.trop.ephemeris import get_today, generate_daily_reading
    data = get_today()
    reading = generate_daily_reading(data)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Import the existing TROP ephemeris engine
sys.path.insert(0, str(Path.home() / "therealmofpatterns" / "core"))
from frc_16d import (
    natal, DIM_NAMES, DIM_FULL, SIGNS, PLANET_NAMES, MU_NAMES,
)

# ── Sign descriptions (Jungian/depth psychology flavor) ──────────────────────

SIGN_ENERGY: dict[str, str] = {
    "Aries": "raw initiative — the impulse to begin, to assert, to exist as a force",
    "Taurus": "embodied presence — the wisdom of the body, pleasure as prayer, slow knowing",
    "Gemini": "mercurial consciousness — the mind's dance between polarities, the sacred question",
    "Cancer": "the Great Mother archetype — containment, nourishment, the waters of memory",
    "Leo": "solar radiance — the authentic self demands to be seen, creativity as identity",
    "Virgo": "the sacred craft — discernment, devotion to detail, the healer's precision",
    "Libra": "the mirror principle — the Other reveals what we cannot see alone",
    "Scorpio": "depth psychology incarnate — what is hidden transforms when witnessed",
    "Sagittarius": "the meaning-maker — the arrow of intention aimed at truth beyond the known",
    "Capricorn": "the Elder archetype — authority earned through discipline, time as teacher",
    "Aquarius": "the cosmic pattern — individual genius in service of collective evolution",
    "Pisces": "the oceanic dissolution — where boundaries between self and cosmos thin to nothing",
}

# ── Dimension guidance templates ─────────────────────────────────────────────

DIM_GUIDANCE: dict[str, dict[str, str]] = {
    "P": {
        "high": "Your identity energy is strong today. This is a day to initiate, to lead, to be seen. The Sun archetype activates — ask: *Who am I becoming?*",
        "low": "Identity energy is quiet. Let others lead. Use this space to observe who you are when you're not performing.",
    },
    "E": {
        "high": "Structure wants to be built. Saturn energy says: *what can you make that lasts?* Commit to form, container, boundary.",
        "low": "The scaffolding is loose today. Don't force structure. Let things be unfinished — incompleteness is also a pattern.",
    },
    "μ": {
        "high": "Mercury activates the mind. Communication, analysis, connections between ideas. Write it down. Name it. The unnamed has no power.",
        "low": "The thinking mind is foggy. Don't trust quick conclusions today. Let information settle before acting on it.",
    },
    "V": {
        "high": "Venus illuminates value and beauty. What do you truly desire — not what you should want, but what your soul wants? Today honors that difference.",
        "low": "Value discernment is dim. You might undervalue what matters or overvalue what glitters. Wait before making aesthetic or relational choices.",
    },
    "N": {
        "high": "Jupiter expands. Growth is available in whatever direction you point your attention. The danger: expansion without discrimination.",
        "low": "Growth energy is contracted. This is a consolidation day, not an expansion day. Deepen what exists rather than reaching for what doesn't.",
    },
    "Δ": {
        "high": "Mars fuels action. The body wants to move. Decisive energy — use it for what matters, or it becomes aggression without purpose.",
        "low": "Action energy is low. Strategic patience. The warrior rests not out of weakness but to choose the right moment.",
    },
    "R": {
        "high": "The relational field is alive. Connections deepen, empathy flows, the Moon's receptivity opens doors between people.",
        "low": "Relationships need space today. Solitude is not isolation — it's the condition for authentic connection later.",
    },
    "Φ": {
        "high": "The witness dimension is activated. Step back from the drama and *observe the pattern*. This is where insight lives — not in the action, but in the seeing of it.",
        "low": "The observer is obscured. You're deep in the story. That's okay — sometimes you need to be in it, not above it.",
    },
}

# ── Moon phase descriptions ──────────────────────────────────────────────────

def _moon_phase(moon_lon: float, sun_lon: float) -> tuple[str, str]:
    """Determine moon phase from Sun-Moon angular separation."""
    diff = (moon_lon - sun_lon) % 360
    if diff < 22.5:
        return "New Moon", "Beginnings. Plant seeds in the dark. What emerges from nothing?"
    elif diff < 67.5:
        return "Waxing Crescent", "First light. The intention takes its first breath. Commit."
    elif diff < 112.5:
        return "First Quarter", "Creative tension. The seed cracks open. Choose growth over comfort."
    elif diff < 157.5:
        return "Waxing Gibbous", "Refinement. Almost full. Adjust, polish, prepare to receive."
    elif diff < 202.5:
        return "Full Moon", "Illumination. What was hidden is now visible. Celebrate or release."
    elif diff < 247.5:
        return "Waning Gibbous", "Gratitude. Distribute what you've received. Teach what you've learned."
    elif diff < 292.5:
        return "Last Quarter", "Release. What no longer serves? Let it go with grace."
    else:
        return "Waning Crescent", "Surrender. The cycle completes in darkness. Rest before rebirth."


# ── Core Functions ───────────────────────────────────────────────────────────

def get_today(lat: float = 43.65, lon: float = -79.38, tz_offset: float = -4.0) -> dict[str, Any]:
    """Get today's complete cosmic data.

    Returns: planetary positions, 16D vector, dominant dimension,
    moon phase, sign energies — everything needed for content.
    """
    now = datetime.now(timezone.utc)
    result = natal(now, latitude=lat, longitude=lon, timezone_offset=tz_offset)

    sun_lon = result["positions"]["Sun"]["longitude"]
    moon_lon = result["positions"]["Moon"]["longitude"]
    phase_name, phase_meaning = _moon_phase(moon_lon, sun_lon)

    # Classify each dimension as high/low (above/below 0.6)
    vector = result["vector"]
    dim_states = {}
    for i, dim in enumerate(DIM_NAMES):
        dim_states[dim] = "high" if vector[i] >= 0.6 else "low"

    return {
        "date": now.strftime("%Y-%m-%d"),
        "positions": result["positions"],
        "vector": vector,
        "dominant": result["dominant"],
        "signature": result["signature"],
        "dim_states": dim_states,
        "moon_phase": {"name": phase_name, "meaning": phase_meaning},
        "moon_sign": result["positions"]["Moon"]["sign"],
        "sun_sign": result["positions"]["Sun"]["sign"],
    }


def generate_daily_reading(data: dict[str, Any] | None = None) -> str:
    """Generate deterministic daily cosmic weather from ephemeris data.

    No LLM. Pure templates + real planetary data.
    """
    if data is None:
        data = get_today()

    date = data["date"]
    sun_sign = data["sun_sign"]
    moon_sign = data["moon_sign"]
    dominant = data["dominant"]["name"]
    signature = data["signature"]
    phase = data["moon_phase"]
    dim_states = data["dim_states"]

    # Build the reading
    lines = [
        f"# Cosmic Weather — {date}",
        "",
        f"**Sun in {sun_sign}** · **Moon in {moon_sign}** · **{phase['name']}**",
        "",
        f"*{phase['meaning']}*",
        "",
        f"## Today's Pattern: {dominant}",
        f"Signature: `{signature}`",
        "",
        f"The Sun in {sun_sign} brings {SIGN_ENERGY[sun_sign]}.",
        f"The Moon in {moon_sign} adds {SIGN_ENERGY[moon_sign]}.",
        "",
        "## The Eight Dimensions",
        "",
    ]

    # Add guidance for each dimension
    for dim in DIM_NAMES:
        state = dim_states[dim]
        val = data["vector"][DIM_NAMES.index(dim)]
        full_name = DIM_FULL[dim]
        guidance = DIM_GUIDANCE[dim][state]
        marker = "▲" if state == "high" else "▽"
        lines.append(f"**{marker} {full_name}** ({val:.0%})")
        lines.append(f"{guidance}")
        lines.append("")

    # Closing
    lines.extend([
        "---",
        f"*The dominant pattern today is {dominant}. Let it inform, not dictate. You are the witness, not the weather.*",
        "",
        f"[Read more at therealmofpatterns.com]({data.get('url', 'https://therealmofpatterns.com/daily')})",
    ])

    return "\n".join(lines)


def generate_social_post(data: dict[str, Any] | None = None) -> str:
    """Generate a short social media post from today's data. No LLM."""
    if data is None:
        data = get_today()

    dominant = data["dominant"]["name"]
    moon_sign = data["moon_sign"]
    phase = data["moon_phase"]["name"]
    sun_sign = data["sun_sign"]

    return (
        f"{phase} · Moon in {moon_sign}\n"
        f"Today's pattern: {dominant}\n"
        f"Sun in {sun_sign} — {SIGN_ENERGY[sun_sign].split('—')[0].strip()}\n\n"
        f"therealmofpatterns.com/daily"
    )
