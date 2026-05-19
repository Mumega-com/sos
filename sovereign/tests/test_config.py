"""Tests for kernel.config — env-var driven defaults."""

import importlib


def test_defaults_without_env(monkeypatch):
    """Config falls back to sensible localhost defaults when env vars are absent."""
    for key in ["MIRROR_URL", "SQUAD_URL", "SOS_ENGINE_URL", "REDIS_URL"]:
        monkeypatch.delenv(key, raising=False)
    import kernel.config as cfg
    importlib.reload(cfg)
    assert cfg.MIRROR_URL == "http://localhost:8844"
    assert cfg.SQUAD_URL == "http://localhost:8060"
    assert cfg.SOS_ENGINE_URL == "http://localhost:6060"
    assert cfg.REDIS_URL == "redis://localhost:6379/0"


def test_env_override(monkeypatch):
    """Config picks up values from environment variables."""
    monkeypatch.setenv("MIRROR_URL", "http://mirror.example.com:8844")
    import kernel.config as cfg
    importlib.reload(cfg)
    assert cfg.MIRROR_URL == "http://mirror.example.com:8844"


def test_squad_url_override(monkeypatch):
    """SQUAD_URL env var overrides the default."""
    monkeypatch.setenv("SQUAD_URL", "http://squad.example.com:8060")
    import kernel.config as cfg
    importlib.reload(cfg)
    assert cfg.SQUAD_URL == "http://squad.example.com:8060"


def test_redis_password_override(monkeypatch):
    """REDIS_PASSWORD env var is picked up."""
    monkeypatch.setenv("REDIS_PASSWORD", "s3cr3t")
    import kernel.config as cfg
    importlib.reload(cfg)
    assert cfg.REDIS_PASSWORD == "s3cr3t"
