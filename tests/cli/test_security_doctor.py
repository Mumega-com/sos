from __future__ import annotations

from sos.cli.security import redis_security_findings


def _levels(env: dict[str, str]) -> dict[str, str]:
    return {finding.name: finding.level for finding in redis_security_findings(env)}


def test_redis_security_allows_authenticated_production_rediss() -> None:
    levels = _levels({"SOS_ENV": "production", "REDIS_URL": "rediss://:secret@redis.example.com:6379/0"})

    assert levels == {"Redis config": "ok"}


def test_redis_security_fails_open_localhost_production() -> None:
    levels = _levels({"SOS_ENV": "production", "REDIS_URL": "redis://localhost:6379/0"})

    assert levels["Redis production host"] == "fail"
    assert levels["Redis auth"] == "fail"


def test_redis_security_warns_on_dev_open_localhost() -> None:
    levels = _levels({"SOS_ENV": "development", "REDIS_URL": "redis://localhost:6379/0"})

    assert levels["Redis local auth"] == "warn"


def test_redis_security_warns_on_duplicate_auth_sources() -> None:
    levels = _levels(
        {
            "REDIS_PASSWORD": "secret",
            "REDIS_URL": "redis://:other@localhost:6379/0",
        }
    )

    assert levels["Redis duplicate auth"] == "warn"
