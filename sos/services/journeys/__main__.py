"""Journey CLI.

Usage:
    python -m sos.services.journeys --agent worker --status
    python -m sos.services.journeys --agent worker --recommend
    python -m sos.services.journeys --agent worker --start builder
    python -m sos.services.journeys --agent worker --evaluate
    python -m sos.services.journeys --leaderboard
    python -m sos.services.journeys --leaderboard --path builder
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from sos.services.journeys.tracker import JourneyTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="SOS Journey System")
    parser.add_argument("--agent", help="Agent name")
    parser.add_argument("--status", action="store_true", help="Show journey progress")
    parser.add_argument("--recommend", action="store_true", help="Recommend a journey")
    parser.add_argument("--start", help="Start a journey path (e.g., builder)")
    parser.add_argument("--evaluate", action="store_true", help="Auto-evaluate milestones")
    parser.add_argument("--leaderboard", action="store_true", help="Show leaderboard")
    parser.add_argument("--path", help="Filter leaderboard by path")
    args = parser.parse_args()

    tracker = JourneyTracker()

    if args.leaderboard:
        leaders = tracker.get_leaderboard(args.path)
        if not leaders:
            print("No journey progress yet.")
            return
        print(f"\n{'Agent':<15} {'Path':<12} {'Done':>4} {'Current':<20} {'Badges':>6} {'MIND':>6}")
        print("-" * 70)
        for l in leaders:
            print(f"{l['agent']:<15} {l['path']:<12} {l['completed']:>4} {l['current']:<20} {l['badges']:>6} {l['total_mind']:>6}")
        return

    if not args.agent:
        parser.error("--agent required (except for --leaderboard)")

    if args.recommend:
        path = tracker.recommend_journey(args.agent)
        path_def = tracker.paths.get(path, {})
        print(f"Recommended journey for {args.agent}: {path_def.get('display', path)}")
        print(f"  {path_def.get('description', '')}")
        return

    if args.start:
        result = tracker.start_journey(args.agent, args.start)
        if result.get("error"):
            print(f"Error: {result['error']}")
            sys.exit(1)
        print(f"Journey started: {result['display']}")
        print(f"  First milestone: {result['current_milestone']}")
        return

    if args.evaluate:
        completions = tracker.auto_evaluate(args.agent)
        if not completions:
            print(f"No milestones completed for {args.agent}.")
        for c in completions:
            print(f"  Completed: {c['title']} (+{c['reward_mind']} MIND, badge: {c['badge']})")
        return

    if args.status:
        progress = tracker.check_progress(args.agent)
        if not progress:
            rec = tracker.recommend_journey(args.agent)
            print(f"{args.agent} has no active journeys.")
            print(f"Recommended: {rec} — start with: --start {rec}")
            return
        for p in progress:
            print(f"\n{p['display']} ({p['path']})")
            print(f"  Progress: {p['completed']}/{p['total']} ({p['progress_pct']}%)")
            print(f"  Current: {p['current_title']} ({p['current_milestone']})")
            if p["badges"]:
                print(f"  Badges: {', '.join(p['badges'])}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
