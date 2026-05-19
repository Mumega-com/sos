#!/usr/bin/env python3
"""
Mumega CLI - Sovereign Operating System for AI Agents

Usage:
    mumega [command] [options]

Commands:
    start       Start all services
    engine      Start engine service only
    memory      Start memory service only
    autonomy    Start autonomy service only
    doctor      Check system health
    chat        Interactive chat with an agent
    status      Show service status
    version     Show version info
"""

import argparse
import asyncio
import sys

from sos.observability.logging import get_logger

log = get_logger("cli")

__version__ = "0.1.0"


def cmd_version(args):
    """Show version info."""
    print(f"mumega {__version__}")
    print("Sovereign Operating System for AI Agents")
    print("https://mumega.com")


def cmd_doctor(args):
    """Run comprehensive system health checks."""
    import os
    import httpx
    from pathlib import Path

    print("Mumega Doctor v0.2.0")
    print("=" * 50)

    errors = 0
    warnings = 0

    def check_ok(name, value):
        print(f"[OK] {name}: {value}")

    def check_warn(name, value):
        nonlocal warnings
        warnings += 1
        print(f"[!!] {name}: {value}")

    def check_fail(name, value):
        nonlocal errors
        errors += 1
        print(f"[XX] {name}: {value}")

    def check_skip(name, value):
        print(f"[--] {name}: {value}")

    # Stage 1: Environment
    print("\n--- Environment ---")

    py_version = sys.version_info
    if py_version >= (3, 10):
        check_ok("Python", f"{py_version.major}.{py_version.minor}.{py_version.micro}")
    else:
        check_fail("Python", f"{py_version.major}.{py_version.minor} (need 3.10+)")

    # Check required packages
    packages = [
        ("fastapi", "FastAPI"),
        ("httpx", "HTTPX"),
        ("pydantic", "Pydantic"),
        ("uvicorn", "Uvicorn"),
    ]
    for pkg, name in packages:
        try:
            mod = __import__(pkg)
            version = getattr(mod, "__version__", "installed")
            check_ok(name, version)
        except ImportError:
            check_fail(name, "not installed")

    # Stage 2: Configuration
    print("\n--- Configuration ---")

    # Check .env file
    env_file = Path.cwd() / ".env"
    env_example = Path.cwd() / ".env.example"
    if env_file.exists():
        check_ok(".env", "found")
    elif env_example.exists():
        check_warn(".env", "missing (copy from .env.example)")
    else:
        check_warn(".env", "missing")

    # Check model API keys
    model_keys = [
        ("GEMINI_API_KEY", "Gemini"),
        ("ANTHROPIC_API_KEY", "Claude"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("XAI_API_KEY", "Grok"),
    ]
    has_model = False
    for key, name in model_keys:
        val = os.getenv(key)
        if val:
            check_ok(name, f"configured ({val[:8]}...)")
            has_model = True
            break

    if not has_model:
        check_warn("Model API", "no API key found (set GEMINI_API_KEY)")

    # Gateway URL
    gateway = os.getenv("GATEWAY_URL", "https://gateway.mumega.com/")
    check_ok("Gateway", gateway)

    # Stage 3: Services
    print("\n--- Services ---")

    services = [
        ("Engine", os.getenv("SOS_ENGINE_URL", "http://localhost:6060")),
        ("Memory", os.getenv("SOS_MEMORY_URL", "http://localhost:6061")),
        ("Economy", os.getenv("SOS_ECONOMY_URL", "http://localhost:6062")),
        ("Tools", os.getenv("SOS_TOOLS_URL", "http://localhost:6063")),
    ]

    for name, url in services:
        try:
            resp = httpx.get(f"{url}/health", timeout=2.0)
            if resp.status_code == 200:
                check_ok(name, f"running at {url}")
            else:
                check_warn(name, f"unhealthy ({resp.status_code})")
        except Exception:
            check_skip(name, "not running")

    # Stage 4: Security
    print("\n--- Security ---")

    # Check state directory permissions
    sos_home = Path(os.getenv("SOS_HOME", Path.home() / ".sos"))
    if sos_home.exists():
        mode = oct(sos_home.stat().st_mode)[-3:]
        if mode in ("700", "750", "755"):
            check_ok("SOS_HOME", f"{sos_home} (mode {mode})")
        else:
            check_warn("SOS_HOME", f"permissions {mode} (recommend 700)")
    else:
        check_skip("SOS_HOME", "not created yet")

    # Check for common secrets in environment
    dangerous_in_env = ["PASSWORD", "SECRET", "PRIVATE_KEY"]
    for key in os.environ:
        for danger in dangerous_in_env:
            if danger in key and key not in ["GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"]:
                check_warn("Secrets", f"{key} in environment (use secret store)")
                break

    # Stage 5: Imports
    print("\n--- Core Modules ---")

    core_imports = [
        ("sos.kernel.config", "Kernel Config"),
        ("sos.services.engine.core", "Engine Core"),
        ("sos.services.engine.resilience", "Resilience"),
        ("sos.kernel.dreams", "Dreams"),
        ("sos.contracts.errors", "Error Codes"),
    ]

    for module, name in core_imports:
        try:
            __import__(module)
            check_ok(name, "OK")
        except Exception as e:
            check_fail(name, f"import failed: {e}")

    # Summary
    print("\n" + "=" * 50)
    if errors:
        print(f"FAILED: {errors} error(s), {warnings} warning(s)")
        return 1
    elif warnings:
        print(f"PASSED with {warnings} warning(s)")
        return 0
    else:
        print("All checks passed!")
        return 0


def cmd_status(args):
    """Show service status."""
    import httpx

    services = [
        ("Engine", "http://localhost:6060/health"),
        ("Memory", "http://localhost:6061/health"),
        ("Economy", "http://localhost:6062/health"),
        ("Tools", "http://localhost:6063/health"),
    ]

    print("Service Status")
    print("=" * 40)

    for name, url in services:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                print(f"[OK] {name}: running")
            else:
                print(f"[!!] {name}: unhealthy ({resp.status_code})")
        except Exception:
            print(f"[--] {name}: not running")


def cmd_chat(args):
    """Interactive chat with pluggable frontend."""
    from sos.cli.frontends import get_frontend, list_frontends, ChatConfig

    # Build config from args
    config = ChatConfig(
        agent=args.agent or "river",
        engine_url=args.engine_url or "http://localhost:6060",
        streaming=not args.no_stream,
        show_metadata=args.verbose,
    )

    # Get frontend
    frontend_name = args.frontend or "repl"
    try:
        frontend = get_frontend(frontend_name, config)
    except ValueError as e:
        print(f"Error: {e}")
        print(f"Available frontends: {', '.join(list_frontends())}")
        return 1

    # Run frontend
    asyncio.run(frontend.run())


def cmd_start(args):
    """Start services."""
    service = args.service or "engine"

    if service == "engine":
        from sos.services.engine.__main__ import main
        main()
    elif service == "memory":
        from sos.services.memory.__main__ import main
        main()
    elif service == "autonomy":
        from sos.services.autonomy.__main__ import main
        main()
    else:
        print(f"Unknown service: {service}")
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="mumega",
        description="Sovereign Operating System for AI Agents",
    )
    parser.add_argument("--version", "-v", action="store_true", help="Show version")

    subparsers = parser.add_subparsers(dest="command")

    # version
    subparsers.add_parser("version", help="Show version info")

    # doctor
    subparsers.add_parser("doctor", help="Check system health")

    # status
    subparsers.add_parser("status", help="Show service status")

    # chat
    chat_parser = subparsers.add_parser("chat", help="Interactive chat")
    chat_parser.add_argument("--agent", "-a", default="river", help="Agent to chat with")
    chat_parser.add_argument("--frontend", "-f", default="repl", help="Frontend: repl, tui (future)")
    chat_parser.add_argument("--engine-url", "-e", default="http://localhost:6060", help="Engine URL")
    chat_parser.add_argument("--no-stream", action="store_true", help="Disable streaming")
    chat_parser.add_argument("--verbose", "-V", action="store_true", help="Show model metadata")

    # start
    start_parser = subparsers.add_parser("start", help="Start a service")
    start_parser.add_argument("service", nargs="?", default="engine",
                              choices=["engine", "memory", "autonomy", "all"],
                              help="Service to start")

    args = parser.parse_args()

    if args.version:
        return cmd_version(args)

    if args.command == "version":
        return cmd_version(args)
    elif args.command == "doctor":
        return cmd_doctor(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "chat":
        return cmd_chat(args)
    elif args.command == "start":
        return cmd_start(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
