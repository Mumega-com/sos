"""
SOS Doctor - sanity checks for local deployments.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple
import os
import json
import re

from sos.kernel import Config
from sos.observability.logging import get_logger

log = get_logger("sos_doctor")

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z-_]{20,}"),
    re.compile(r"xai-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH) PRIVATE KEY-----"),
]

EXCLUDE_DIRS = {".git", "node_modules", "dist", "build", ".next", ".sos", ".venv", "__pycache__"}


def _scan_secrets(root: Path) -> List[Tuple[str, str]]:
    hits = []
    for path in root.rglob("*"):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.is_dir():
            continue
        if path.name.startswith(".env"):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append((str(path), pattern.pattern))
                break
    return hits


def run_doctor() -> int:
    cfg = Config.load()
    issues = []

    # Paths check
    for name, path in [
        ("config", cfg.paths.config_dir),
        ("data", cfg.paths.data_dir),
        ("logs", cfg.paths.logs_dir),
        ("plugins", cfg.paths.plugins_dir),
    ]:
        if not path.exists():
            issues.append(f"Missing {name} dir: {path}")

    # Plugin trust check
    if cfg.paths.plugins_dir.exists():
        for plugin_dir in cfg.paths.plugins_dir.iterdir():
            manifest = plugin_dir / "plugin.json"
            if not manifest.exists():
                continue
            try:
                data = json.loads(manifest.read_text())
                trust = data.get("trust_level", "community")
                env = (os.getenv("SOS_ENV", "development") or "development").lower()
                if env == "production" and trust == "unsigned":
                    issues.append(f"Unsigned plugin in production: {manifest}")
            except Exception as exc:
                issues.append(f"Invalid plugin manifest: {manifest} ({exc})")

    # Secret scan
    repo_root = Path(__file__).resolve().parents[2]
    secret_hits = _scan_secrets(repo_root)
    for hit_path, pattern in secret_hits:
        issues.append(f"Potential secret in {hit_path} (pattern {pattern})")

    if issues:
        log.error("Doctor found issues:")
        for item in issues:
            log.error(f"- {item}")
        return 1

    log.info("Doctor checks passed.")
    return 0
