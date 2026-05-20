"""Security checks shared by SOS CLI commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


PRODUCTION_VALUES = {"prod", "production", "live"}


@dataclass(frozen=True)
class SecurityFinding:
    level: str
    name: str
    detail: str


def _is_production(env: Mapping[str, str]) -> bool:
    for key in ("SOS_ENV", "APP_ENV", "ENVIRONMENT", "NODE_ENV"):
        if env.get(key, "").strip().lower() in PRODUCTION_VALUES:
            return True
    return False


def _url_has_password(redis_url: str) -> bool:
    parsed = urlparse(redis_url)
    return bool(parsed.password)


def redis_security_findings(env: Mapping[str, str]) -> list[SecurityFinding]:
    """Return Redis safety findings for local doctor output.

    Local development is allowed to run unauthenticated localhost Redis. A
    production deployment must not.
    """

    redis_url = env.get("REDIS_URL") or env.get("SOS_REDIS_URL") or "redis://localhost:6379/0"
    parsed = urlparse(redis_url)
    host = (parsed.hostname or env.get("REDIS_HOST") or "localhost").lower()
    scheme = parsed.scheme or "redis"
    password = env.get("REDIS_PASSWORD", "")
    production = _is_production(env)
    has_auth = bool(password) or _url_has_password(redis_url)
    localhost = host in {"localhost", "127.0.0.1", "::1"}

    findings: list[SecurityFinding] = []
    if production and localhost:
        findings.append(
            SecurityFinding(
                "fail",
                "Redis production host",
                f"{redis_url} points at localhost in production",
            )
        )
    elif localhost and not has_auth:
        findings.append(
            SecurityFinding(
                "warn",
                "Redis local auth",
                "unauthenticated localhost Redis is acceptable for dev only",
            )
        )

    if production and not has_auth:
        findings.append(
            SecurityFinding(
                "fail",
                "Redis auth",
                "production Redis requires REDIS_PASSWORD or password-bearing REDIS_URL",
            )
        )

    if production and scheme == "redis" and not localhost:
        findings.append(
            SecurityFinding(
                "fail",
                "Redis TLS",
                "production remote Redis should use rediss:// or private network transport",
            )
        )

    if password and _url_has_password(redis_url):
        findings.append(
            SecurityFinding(
                "warn",
                "Redis duplicate auth",
                "set either REDIS_PASSWORD or password-bearing REDIS_URL, not both",
            )
        )

    if not findings:
        findings.append(SecurityFinding("ok", "Redis config", "safe for declared environment"))
    return findings
