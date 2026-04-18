"""Dashboard constants — loaded once at import time."""
from __future__ import annotations

from pathlib import Path

from sos.kernel.settings import get_settings as _get_settings

_dash_settings = _get_settings()
REDIS_PASSWORD: str = _dash_settings.redis.password_str
# Note: dashboard historically defaulted SQUAD_URL to localhost (no 127.0.0.1).
# Preserved via settings.services.squad_url → falls through to SQUAD_URL env.
SQUAD_URL: str = _dash_settings.services.squad_url
MIRROR_URL: str = _dash_settings.services.mirror
COOKIE_NAME: str = "mum_dash"
TOKENS_PATH: Path = Path(__file__).resolve().parent.parent.parent / "bus" / "tokens.json"
