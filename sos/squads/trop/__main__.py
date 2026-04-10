"""TROP Squad CLI.

Usage:
    python -m sos.squads.trop daily     # Daily cosmic content + distribute
    python -m sos.squads.trop weekly    # Weekly deep content
    python -m sos.squads.trop monthly   # Monthly tarot + I Ching
    python -m sos.squads.trop social    # Social media posting
    python -m sos.squads.trop health    # Health check
    python -m sos.squads.trop all       # Run all workflows
"""

from __future__ import annotations

import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from sos.squads.trop.workflows import (
    daily_content,
    weekly_content,
    monthly_content,
    social_post,
    health_check,
)
from sos.squads.trop.video_pipeline import run_pipeline, run_batch_signs


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "daily":
        result = daily_content()
    elif cmd == "weekly":
        result = weekly_content()
    elif cmd == "monthly":
        result = monthly_content()
    elif cmd == "social":
        result = social_post()
    elif cmd == "health":
        result = health_check()
    elif cmd == "video":
        result = run_pipeline()
    elif cmd == "video-sign":
        sign = sys.argv[2] if len(sys.argv) > 2 else "Aries"
        result = run_pipeline(sign=sign)
    elif cmd == "video-batch":
        result = run_batch_signs()
    elif cmd == "all":
        print("=== Daily ===")
        daily_content()
        print("=== Video ===")
        run_pipeline()
        print("=== Social ===")
        social_post()
        print("=== Health ===")
        health_check()
        return
    else:
        print(__doc__)
        return

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
