"""Dashboard constants — loaded once at import time."""
from __future__ import annotations

from pathlib import Path
import os

from sos.kernel.settings import get_settings as _get_settings

_dash_settings = _get_settings()
REDIS_PASSWORD: str = _dash_settings.redis.password_str
# Note: dashboard historically defaulted SQUAD_URL to localhost (no 127.0.0.1).
# Preserved via settings.services.squad_url → falls through to SQUAD_URL env.
SQUAD_URL: str = _dash_settings.services.squad_url
MIRROR_URL: str = _dash_settings.services.mirror
COOKIE_NAME: str = os.getenv("SOS_DASHBOARD_COOKIE_NAME", "sos_dash")
TOKENS_PATH: Path = Path(os.getenv("SOS_BUS_TOKENS_PATH", str(Path.home() / ".sos" / "tokens.json")))
