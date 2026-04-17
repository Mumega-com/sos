"""Dashboard constants — loaded once at import time."""
from __future__ import annotations

import os
from pathlib import Path

REDIS_PASSWORD: str = os.environ.get("REDIS_PASSWORD", "")
SQUAD_URL: str = os.environ.get("SQUAD_URL", "http://localhost:8060")
MIRROR_URL: str = os.environ.get("MIRROR_URL", "http://localhost:8844")
COOKIE_NAME: str = "mum_dash"
TOKENS_PATH: Path = Path(__file__).resolve().parent.parent.parent / "bus" / "tokens.json"
