"""SOS CLI package."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sos import __version__
from sos.cli.frontends import ChatConfig, get_frontend, list_frontends
from sos.observability.logging import get_logger

log = get_logger("cli")


def cmd_version(args):
    """Show version info."""
    print(f"mumega {__version__}")
    print("Sovereign Operating System for AI Agents")
    print("https://github.com/Mumega-com/sos")


def cmd_doctor(args):
    """Run basic system health checks."""
    import httpx

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

    print("\n--- Environment ---")
    py_version = sys.version_info
    if py_version >= (3, 10):
        check_ok("Python", f"{py_version.major}.{py_version.minor}.{py_version.micro}")
    else:
        check_fail("Python", f"{py_version.major}.{py_version.minor} (need 3.10+)")

    for pkg, name in (
        ("fastapi", "FastAPI"),
        ("httpx", "HTTPX"),
        ("pydantic", "Pydantic"),
        ("uvicorn", "Uvicorn"),
    ):
        try:
            mod = __import__(pkg)
            check_ok(name, getattr(mod, "__version__", "installed"))
        except ImportError:
            check_fail(name, "not installed")

    print("\n--- Configuration ---")
    env_file = Path.cwd() / ".env"
    env_example = Path.cwd() / ".env.example"
    if env_file.exists():
        check_ok(".env", "found")
    elif env_example.exists():
        check_warn(".env", "missing (copy from .env.example)")
    else:
        check_warn(".env", "missing")

    model_keys = (
        ("GEMINI_API_KEY", "Gemini"),
        ("ANTHROPIC_API_KEY", "Claude"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("XAI_API_KEY", "Grok"),
    )
    has_model = False
    for key, name in model_keys:
        val = os.getenv(key)
        if val:
            check_ok(name, f"configured ({val[:8]}...)")
            has_model = True
            break
    if not has_model:
        check_warn("GEMINI_API_KEY", "missing (set a model API key)")

    from sos.cli.security import redis_security_findings

    for finding in redis_security_findings(os.environ):
        if finding.level == "ok":
            check_ok(finding.name, finding.detail)
        elif finding.level == "warn":
            check_warn(finding.name, finding.detail)
        else:
            check_fail(finding.name, finding.detail)

    print("\n--- Services ---")
    services = (
        ("Engine", os.getenv("SOS_ENGINE_URL", "http://localhost:6060")),
        ("Memory", os.getenv("SOS_MEMORY_URL", "http://localhost:6061")),
        ("Economy", os.getenv("SOS_ECONOMY_URL", "http://localhost:6062")),
        ("Tools", os.getenv("SOS_TOOLS_URL", "http://localhost:6063")),
    )
    for name, url in services:
        try:
            resp = httpx.get(f"{url}/health", timeout=2.0)
            if resp.status_code == 200:
                check_ok(name, f"running at {url}")
            else:
                check_warn(name, f"unhealthy ({resp.status_code})")
        except Exception:
            check_skip(name, "not running")

    print("\n" + "=" * 50)
    if errors:
        print(f"FAILED: {errors} error(s), {warnings} warning(s)")
        return 1
    if warnings:
        print(f"PASSED with {warnings} warning(s)")
        return 0
    print("All checks passed!")
    return 0


def cmd_status(args):
    """Show service status."""
    import httpx

    print("Service Status")
    print("=" * 40)
    for name, url in (
        ("Engine", "http://localhost:6060/health"),
        ("Memory", "http://localhost:6061/health"),
        ("Economy", "http://localhost:6062/health"),
        ("Tools", "http://localhost:6063/health"),
    ):
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                print(f"[OK] {name}: running")
            else:
                print(f"[!!] {name}: unhealthy ({resp.status_code})")
        except Exception:
            print(f"[--] {name}: not running")


def _repo_root() -> Path:
    """Find the repository root for the installed package.

    Prefers the git toplevel of the package directory; falls back to cwd.
    """
    import subprocess

    pkg_dir = Path(__file__).resolve().parent
    for candidate in (pkg_dir, Path.cwd()):
        try:
            out = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            )
            return Path(out.stdout.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return Path.cwd()


def cmd_update(args):
    """Pull upstream improvements into a tenant's SOS fork.

    Fast-forwards the local checkout onto ``upstream/main`` and runs a
    post-update ``doctor`` health check as proof. Never force-merges.
    """
    import subprocess

    root = _repo_root()

    def git(*cmd, capture=True):
        return subprocess.run(
            ["git", "-C", str(root), *cmd],
            capture_output=capture,
            text=True,
        )

    # Confirm we are inside a git repo at all.
    inside = git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        print(f"[XX] Not a git repository: {root}")
        print("     Run 'sos update' from inside your SOS fork.")
        return 1

    print(f"SOS Update — repo: {root}")
    print("=" * 50)

    # 1. Require an 'upstream' remote.
    upstream = git("remote", "get-url", "upstream")
    if upstream.returncode != 0:
        print("[XX] No 'upstream' remote configured.")
        print("     Add it with:")
        print("       git remote add upstream https://github.com/Mumega-com/sos.git")
        print("     then re-run 'sos update'.")
        return 1
    print(f"[OK] upstream: {upstream.stdout.strip()}")

    # 2. Fetch upstream.
    print("\nFetching upstream...")
    fetched = git("fetch", "upstream", capture=False)
    if fetched.returncode != 0:
        print("[XX] git fetch upstream failed.")
        return 1

    # 3. Report how many commits behind upstream/main.
    behind = git("rev-list", "--count", "HEAD..upstream/main")
    if behind.returncode != 0:
        print("[XX] Could not compare against upstream/main.")
        print("     Ensure upstream has a 'main' branch.")
        return 1
    behind_count = behind.stdout.strip()
    if behind_count == "0":
        print("[OK] Already up to date with upstream/main.")
        update_failed = False
    else:
        print(f"[!!] {behind_count} commit(s) behind upstream/main.")
        # 4. Fast-forward only — never force.
        print("\nFast-forwarding onto upstream/main...")
        ff = git("merge", "--ff-only", "upstream/main", capture=False)
        if ff.returncode != 0:
            print("[XX] Fast-forward merge failed — local history has diverged.")
            print("     Your fork has commits not in upstream. Merge manually:")
            print("       git fetch upstream")
            print("       git merge upstream/main   # resolve conflicts, then commit")
            print("     (Not force-merging to protect your local changes.)")
            return 1
        print(f"[OK] Updated to upstream/main ({behind_count} commit(s) applied).")
        update_failed = False

    # 5. Post-update health proof via doctor.
    print("\n--- Post-update health check ---")
    try:
        doctor_rc = cmd_doctor(args)
    except Exception as exc:  # doctor may need deps not present in a bare fork
        print(f"[--] doctor skipped: {exc}")
        doctor_rc = 0

    if update_failed:
        return 1
    return 0 if not doctor_rc else doctor_rc


def cmd_chat(args):
    """Interactive chat with pluggable frontend."""
    config = ChatConfig(
        agent=args.agent or "river",
        engine_url=args.engine_url or "http://localhost:6060",
        streaming=not args.no_stream,
        show_metadata=args.verbose,
    )
    frontend = get_frontend(args.frontend or "repl", config)
    return asyncio.run(frontend.run())


def cmd_start(args):
    """Start a service."""
    service = args.service or "engine"
    if service == "engine":
        from sos.services.engine.__main__ import main as engine_main

        return engine_main()
    if service == "memory":
        from sos.services.memory.__main__ import main as memory_main

        return memory_main()
    if service == "autonomy":
        from sos.services.autonomy.__main__ import main as autonomy_main

        return autonomy_main()
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
    subparsers.add_parser("version", help="Show version info")
    subparsers.add_parser("doctor", help="Check system health")
    subparsers.add_parser("status", help="Show service status")
    subparsers.add_parser(
        "update",
        help="Pull upstream SOS improvements into this fork (ff-only)",
    )
    operator_parser = subparsers.add_parser("operator", help="Show compact operator snapshot")
    operator_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")

    local_parser = subparsers.add_parser("local", help="Local public quickstart helpers")
    local_sub = local_parser.add_subparsers(dest="local_command")
    local_sub.add_parser("init", help="Generate local dev tokens and env")
    local_sub.add_parser("migrate", help="Run local Squad SQLite migrations")
    local_sub.add_parser("doctor", help="Run local public smoke doctor")

    chat_parser = subparsers.add_parser("chat", help="Interactive chat")
    chat_parser.add_argument("--agent", "-a", default="river", help="Agent to chat with")
    chat_parser.add_argument("--frontend", "-f", default="repl", help="Frontend: repl")
    chat_parser.add_argument("--engine-url", "-e", default="http://localhost:6060")
    chat_parser.add_argument("--no-stream", action="store_true", help="Disable streaming")
    chat_parser.add_argument("--verbose", "-V", action="store_true")

    start_parser = subparsers.add_parser("start", help="Start a service")
    start_parser.add_argument(
        "service",
        nargs="?",
        default="engine",
        choices=["engine", "memory", "autonomy", "all"],
    )

    args = parser.parse_args()
    if args.version or args.command == "version":
        return cmd_version(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "update":
        return cmd_update(args)
    if args.command == "operator":
        from sos.cli.operator import run_operator_command

        return run_operator_command(args)
    if args.command == "local":
        from sos.cli.local import init_profile, run_migrations, smoke_profile

        if args.local_command == "init":
            env = init_profile()
            print(f"Wrote {env['SOS_HOME']}/local/dev.env")
            return 0
        if args.local_command == "migrate":
            return run_migrations()
        if args.local_command == "doctor":
            return smoke_profile()
        local_parser.print_help()
        return 1
    if args.command == "chat":
        return cmd_chat(args)
    if args.command == "start":
        return cmd_start(args)
    parser.print_help()
    return 0


__all__ = [
    "ChatConfig",
    "cmd_chat",
    "cmd_doctor",
    "cmd_start",
    "cmd_status",
    "cmd_update",
    "cmd_version",
    "get_frontend",
    "list_frontends",
    "main",
]
