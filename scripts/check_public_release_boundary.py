#!/usr/bin/env python3
"""Check that a candidate public tree does not include private SOS surfaces."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
import tomllib
from pathlib import Path


def _repo_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _matches(path: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    return path == pattern or path.startswith(pattern + "/") or fnmatch.fnmatch(path, pattern)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Candidate public repository root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--config-root",
        default=None,
        help="Repository root to read [tool.sos.public_release] rules from.",
    )
    parser.add_argument(
        "--show-ok",
        action="store_true",
        help="Print the loaded boundary rule count when no violation is found.",
    )
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    config_root = Path(args.config_root).resolve() if args.config_root else root
    pyproject = config_root / "pyproject.toml"
    if not pyproject.exists():
        print(f"missing pyproject.toml under {config_root}", file=sys.stderr)
        return 2

    config = tomllib.loads(pyproject.read_text())
    release = config.get("tool", {}).get("sos", {}).get("public_release", {})
    forbidden_paths = release.get("forbidden_paths", [])

    if not forbidden_paths:
        print("no [tool.sos.public_release].forbidden_paths configured", file=sys.stderr)
        return 2

    violations: list[tuple[str, str]] = []
    for path in _repo_files(root):
        for pattern in forbidden_paths:
            if _matches(path, pattern):
                violations.append((path, pattern))
                break

    if violations:
        print("public release boundary violations found:")
        for path, pattern in violations[:200]:
            print(f"- {path}  (matched {pattern})")
        if len(violations) > 200:
            print(f"- ... {len(violations) - 200} more")
        return 1

    if args.show_ok:
        print(f"public release boundary clean ({len(forbidden_paths)} rule(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

