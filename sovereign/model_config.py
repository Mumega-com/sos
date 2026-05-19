"""
Model configuration for the sovereign loop.
Change model names here; no code changes needed.
Read from MODEL_CONFIG env var (JSON) if set, otherwise use defaults below.
"""
import json
import os

_defaults = {
    "tier0_vertex_model": "gemini-2.5-flash",       # Vertex AI ADC path
    "tier1_primary": "gemini-2.5-flash",             # Gemini API key tier 1
    "tier2_github": "gpt-4o-mini",                   # GitHub Models
    "tier3_fallback": "gemini-2.5-flash",            # Gemini API key tier 3
    "tier4_openrouter": "openrouter/free",
    "tier5_local": "gemma2:2b",
}


def get() -> dict:
    raw = os.environ.get("MODEL_CONFIG")
    if raw:
        try:
            return {**_defaults, **json.loads(raw)}
        except Exception:
            pass
    return dict(_defaults)
