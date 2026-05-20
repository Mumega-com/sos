#!/usr/bin/env python3
"""Check that the public security model docs exist and name key policies."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "docs/security/public-edge-map.md": (
        "Hostname pattern",
        "Auth",
        "Exposure",
        "mcp.<host>",
        "webhooks/ghl/lead",
    ),
    "docs/security/threat-model.md": (
        "Health Policy",
        "Webhook Ingress Policy",
        "CORS Policy",
        "Redis Policy",
        "Residual Risks",
    ),
    "docs/security/public-route-checklist.md": (
        "Authentication And Authorization",
        "Webhooks",
        "Runtime Dependencies",
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
