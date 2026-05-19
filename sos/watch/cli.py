from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import NamedTuple

from .core import (
    DEFAULT_CONFIG_PATH,
    BusWatchConfig,
    BusWatcher,
    write_default_config,
    write_launchd_plist,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mumega-bus-watch", description="SOS local bus receive bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="Write a starter config and optional launchd plist")
    install.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    install.add_argument("--agent", required=True)
    install.add_argument("--token", help="Bus token. Prefer --token-file, --token-env, or --token-stdin.")
    install.add_argument("--token-file", help="Read bus token from a 0600 local file")
    install.add_argument(
        "--token-env",
        default="SOS_BUS_TOKEN",
        help="Read bus token from this environment variable (default: SOS_BUS_TOKEN)",
    )
    install.add_argument("--token-stdin", action="store_true", help="Read bus token from stdin")
    install.add_argument("--project", default="sos")
    install.add_argument("--launchd", action="store_true")

    run = sub.add_parser("run", help="Poll forever and wake configured transports")
    run.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    run.add_argument("--once", action="store_true")

    doctor = sub.add_parser("doctor", help="Validate config without polling")
    doctor.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    status = sub.add_parser("status", help="Show config and state summary")
    status.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    test_send = sub.add_parser("test-send", help="Send a test message through the bridge")
    test_send.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    test_send.add_argument("--to", required=True)
    test_send.add_argument("--text", default="mumega-bus-watch test")

    args = parser.parse_args(argv)

    if args.command == "install":
        try:
            token_source = _resolve_install_token(args)
        except ValueError as exc:
            print(f"FAIL {exc}")
            return 1
        path = Path(args.config).expanduser()
        write_default_config(
            path,
            agent=args.agent,
            token=token_source.token,
            token_file=token_source.token_file,
            token_env=token_source.token_env,
            project=args.project,
        )
        print(f"wrote {path}")
        if args.launchd:
            plist = write_launchd_plist(path)
            print(f"wrote {plist}")
            print(f"load with: launchctl load {plist}")
        return 0

    try:
        config = BusWatchConfig.load(args.config)
    except Exception as exc:
        print(f"FAIL could not load config: {exc}")
        return 1
    errors = config.validate()

    if args.command == "doctor":
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print("OK config valid")
        return 0

    if args.command == "status":
        print(
            json.dumps(
                {
                    "agent": config.agent,
                    "project": config.project,
                    "bridge_url": config.bridge_url,
                    "state_path": str(config.state_path),
                    "token_source": config.token_source,
                    "token_file": str(config.token_file) if config.token_file else None,
                    "token_env": config.token_env,
                    "transports": [transport.name for transport in config.transports],
                    "config_errors": errors,
                },
                indent=2,
            )
        )
        return 1 if errors else 0

    if args.command == "run":
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        watcher = BusWatcher(config)
        if args.once:
            delivered = watcher.poll_once()
            print(f"delivered {len(delivered)} message(s)")
            return 0
        watcher.run_forever()
        return 0

    if args.command == "test-send":
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        result = BusWatcher(config).send_test(args.to, args.text)
        print(json.dumps(result, indent=2))
        return 0

    parser.print_help()
    return 0


class InstallTokenSource(NamedTuple):
    token: str | None = None
    token_file: str | None = None
    token_env: str | None = None


def _resolve_install_token(args: argparse.Namespace) -> InstallTokenSource:
    explicit_sources = [bool(args.token), bool(args.token_file), bool(args.token_stdin)]
    if sum(explicit_sources) > 1:
        raise ValueError("choose exactly one token source")
    if args.token_file:
        token_file = Path(args.token_file).expanduser()
        token = token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("token source was empty")
        return InstallTokenSource(token_file=str(token_file))
    if args.token_stdin:
        token = sys.stdin.read().strip()
        if not token:
            raise ValueError("token source was empty")
        return InstallTokenSource(token=token)
    if args.token:
        token = args.token.strip()
        if not token:
            raise ValueError("token source was empty")
        return InstallTokenSource(token=token)
    if args.token_env and os.environ.get(args.token_env):
        return InstallTokenSource(token_env=str(args.token_env))
    raise ValueError(
        "token is required; set SOS_BUS_TOKEN or use --token-file, --token-env, or --token-stdin"
    )


if __name__ == "__main__":
    raise SystemExit(main())
