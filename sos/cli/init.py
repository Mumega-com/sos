"""
SOS interactive setup — run with: python -m sos.cli.init

Configures LLM provider, API keys, bus tokens, and Redis.
Use --defaults for non-interactive mode.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

SOS_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = SOS_ROOT / ".env"
TOKENS_FILE = SOS_ROOT / "sos" / "bus" / "tokens.json"

MODELS = ["claude", "openai", "gemini", "ollama", "gemma"]
DEFAULT_MODEL = "gemini"


def green(text: str) -> str:
    return f"\033[1;32m{text}\033[0m"


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def warn(text: str) -> str:
    return f"\033[1;33m{text}\033[0m"


def prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer if answer else default


def gen_token(prefix: str) -> str:
    return f"sk-{prefix}-{secrets.token_hex(16)}"


def gen_password() -> str:
    return secrets.token_urlsafe(24)


def read_env() -> dict[str, str]:
    """Read existing .env into a dict, preserving comments as None values."""
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            env[key.strip()] = value.strip()
    return env


def write_env_var(key: str, value: str) -> None:
    """Set a key in .env — update if exists, append if not."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match both active and commented-out versions
        if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def write_tokens(admin_token: str, agent_token: str) -> None:
    """Write tokens to tokens.json, preserving existing tokens."""
    existing: list[dict] = []
    if TOKENS_FILE.exists():
        try:
            existing = json.loads(TOKENS_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Only add if no admin token exists yet
    existing_tokens = {t.get("token", "") for t in existing}

    new_tokens = []
    if admin_token not in existing_tokens:
        new_tokens.append({
            "token": admin_token,
            "token_hash": "",
            "project": None,
            "label": "Admin — full access (auto-generated)",
            "active": True,
            "created_at": now,
        })
    if agent_token not in existing_tokens:
        new_tokens.append({
            "token": agent_token,
            "token_hash": "",
            "project": None,
            "label": "Default agent (auto-generated)",
            "active": True,
            "created_at": now,
        })

    if new_tokens:
        existing.extend(new_tokens)
        TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKENS_FILE.write_text(json.dumps(existing, indent=2) + "\n")


def test_redis(url: str) -> bool:
    """Test Redis connection."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(url, decode_responses=True)
        return r.ping()
    except Exception:
        return False


def test_mcp_import() -> bool:
    """Test that MCP SSE module can be imported."""
    try:
        import importlib
        importlib.import_module("sos.mcp.sos_mcp_sse")
        return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="SOS interactive setup")
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="Non-interactive mode — use defaults for everything",
    )
    args = parser.parse_args()

    print(f"\n{bold('SOS Setup')}")
    print("=" * 40)

    # Ensure .env exists
    if not ENV_FILE.exists():
        env_example = SOS_ROOT / ".env.example"
        if env_example.exists():
            ENV_FILE.write_text(env_example.read_text())
            print(f"{green('Created')} .env from .env.example")
        else:
            ENV_FILE.write_text("")
            print(f"{green('Created')} empty .env")

    # ── 1. LLM provider ────────────────────────────────────────────
    if args.defaults:
        model = DEFAULT_MODEL
    else:
        model = prompt(
            f"What LLM do you use? ({'/'.join(MODELS)})",
            DEFAULT_MODEL,
        ).lower()
        if model not in MODELS:
            print(f"  Unknown model '{model}', using {DEFAULT_MODEL}")
            model = DEFAULT_MODEL

    write_env_var("SOS_MODEL", model)
    print(f"  Model: {bold(model)}")

    # ── 2. API key ──────────────────────────────────────────────────
    if args.defaults:
        api_key = ""
    else:
        api_key = prompt("API key (or press Enter to skip)")

    if api_key:
        write_env_var("SOS_API_KEY", api_key)
        # Also set the provider-specific key
        key_map = {
            "claude": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "gemma": "GEMINI_API_KEY",
        }
        if model in key_map:
            write_env_var(key_map[model], api_key)
        print(f"  API key: {bold('set')}")
    else:
        print(f"  API key: {warn('skipped')} (set later in .env)")

    # ── 3. Bus tokens ───────────────────────────────────────────────
    admin_token = gen_token("admin")
    agent_token = gen_token("agent")
    write_tokens(admin_token, agent_token)
    print(f"  Admin token: {bold(admin_token[:20])}...")
    print(f"  Agent token: {bold(agent_token[:20])}...")

    # ── 4. Redis password ───────────────────────────────────────────
    redis_pass = gen_password()
    write_env_var("REDIS_PASSWORD", redis_pass)
    redis_url = f"redis://:{redis_pass}@localhost:6379/0"
    write_env_var("SOS_REDIS_URL", redis_url)
    print(f"  Redis password: {bold('generated')}")

    # ── 5. Test Redis ───────────────────────────────────────────────
    # Try the configured URL first, fall back to no-auth localhost
    if test_redis(redis_url):
        print(f"  Redis: {green('connected')} (with password)")
    elif test_redis("redis://localhost:6379/0"):
        print(f"  Redis: {green('connected')} (no auth — update REDIS_PASSWORD in .env)")
        write_env_var("REDIS_PASSWORD", "")
        write_env_var("SOS_REDIS_URL", "redis://localhost:6379/0")
    else:
        print(f"  Redis: {warn('not reachable')} — start it before running SOS")

    # ── 6. Test MCP import ──────────────────────────────────────────
    if test_mcp_import():
        print(f"  MCP SSE: {green('importable')}")
    else:
        print(f"  MCP SSE: {warn('import failed')} — install deps: pip install -r requirements.txt")

    # ── Summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 40)
    print(f"{green('SOS ready.')}\n")
    print(f"  Config:  {ENV_FILE}")
    print(f"  Tokens:  {TOKENS_FILE}")
    print(f"\n  Start with: {bold('docker-compose up')}")
    print(f"  Or manually: {bold('python -m sos.services.engine')}")
    print()


if __name__ == "__main__":
    main()
