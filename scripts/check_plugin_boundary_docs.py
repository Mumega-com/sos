#!/usr/bin/env python3
"""Check that the S081 plugin/profile boundary docs and example exist."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "docs/architecture/plugin-boundary.md": (
        "Contract Shape",
        "Public Kernel Interfaces",
        "OpenClaw/Hermes-Style Adapter",
        "Host Overlays",
        "Compatibility Shims",
        "Minimal Smoke Checklist",
    ),
    "examples/host_profiles/openclaw_hermes_adapter.py": (
        "from sos.sdk import Agent, Message",
        "class HostRuntimeAdapter",
        "def profile_from_env",
    ),
}


def main() -> int:
    failed = False
    for relative, phrases in REQUIRED.items():
        path = ROOT / relative
        if not path.exists():
            print(f"[FAIL] missing {relative}")
            failed = True
            continue
        text = path.read_text(encoding="utf-8")
        missing = [phrase for phrase in phrases if phrase not in text]
        if missing:
            print(f"[FAIL] {relative} missing: {', '.join(missing)}")
            failed = True
        else:
            print(f"[OK] {relative}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
