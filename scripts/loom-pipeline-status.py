#!/usr/bin/env python3
"""Aggregate pipeline status report — Sprint 008 S008-F / G81.

Queries gtm schema across all knights, produces Markdown summary.
Delivers to Discord #mumega-pipeline channel.

Usage:
    python3 scripts/loom-pipeline-status.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger("sos.gtm.pipeline_status")


class PipelineStatusDataSourceError(RuntimeError):
    """Mirror PG unreachable."""


class PipelineStatusDeliveryError(RuntimeError):
    """Discord delivery failed."""


def generate_report(db_url: str) -> str:
    """Query gtm schema and produce Markdown report."""
    import psycopg2

    try:
        conn = psycopg2.connect(db_url)
    except Exception as exc:
        raise PipelineStatusDataSourceError(f"Cannot connect to mirror PG: {exc}") from exc

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    sections: list[str] = []
    sections.append(f"# Pipeline Status Report\n*Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}*\n")

    try:
        with conn.cursor() as cur:
            # Pipeline summary
            cur.execute("""
                SELECT stage, COUNT(*), COALESCE(SUM(value_cents), 0)
                FROM gtm.deals WHERE deleted_at IS NULL
                GROUP BY stage ORDER BY COUNT(*) DESC
            """)
            rows = cur.fetchall()
            sections.append("## Pipeline Summary\n")
            if rows:
                total_deals = sum(r[1] for r in rows)
                total_value = sum(r[2] for r in rows) / 100.0
                sections.append(f"**Total:** {total_deals} deals, ${total_value:,.0f}\n")
                sections.append("| Stage | Count | Value |")
                sections.append("|---|---|---|")
                for stage, count, value in rows:
                    sections.append(f"| {stage} | {count} | ${value/100:,.0f} |")
            else:
                sections.append("*No deals in pipeline.*")
            sections.append("")

            # Stale deals
            cur.execute("""
                SELECT d.id, p.name, c.name, d.stage, d.last_action_at
                FROM gtm.deals d
                LEFT JOIN gtm.people p ON d.person_id = p.id
                LEFT JOIN gtm.companies c ON d.company_id = c.id
                WHERE d.deleted_at IS NULL
                  AND d.stage NOT IN ('closed-won', 'closed-lost')
                  AND d.last_action_at < %s
                ORDER BY d.last_action_at ASC LIMIT 10
            """, (week_ago,))
            stale = cur.fetchall()
            sections.append("## Stale Deals (>7 days no action)\n")
            if stale:
                for _, contact, company, stage, last_action in stale:
                    days = (now - last_action).days if last_action else "?"
                    sections.append(f"- **{contact or 'Unknown'}** / {company or 'Unknown'} — stage: {stage}, {days}d stale")
            else:
                sections.append("*No stale deals.*")
            sections.append("")

            # Hot opportunities
            cur.execute("""
                SELECT cv.summary, cv.occurred_at, cv.participants
                FROM gtm.conversations cv
                WHERE cv.occurred_at > %s
                  AND cv.summary ~* '(buying|budget|timeline|when can|how much|sign|contract|proposal)'
                ORDER BY cv.occurred_at DESC LIMIT 5
            """, (week_ago,))
            hot = cur.fetchall()
            sections.append("## Hot Opportunities (past 7 days)\n")
            if hot:
                for summary, occurred, participants in hot:
                    sections.append(f"- {(summary or '')[:80]}... ({occurred.strftime('%m/%d') if occurred else '?'})")
            else:
                sections.append("*No hot signals detected.*")
            sections.append("")

            # Missing actions
            cur.execute("""
                SELECT COUNT(*) FROM gtm.actions
                WHERE status = 'alerted'
            """)
            alerted_count = cur.fetchone()[0]
            sections.append("## Missing Actions\n")
            sections.append(f"**{alerted_count}** follow-ups missed and alerted.\n")

            # Per-knight breakdown
            cur.execute("""
                SELECT d.owner_knight_id, COUNT(*), COALESCE(SUM(d.value_cents), 0),
                       MAX(d.last_action_at)
                FROM gtm.deals d
                WHERE d.deleted_at IS NULL AND d.owner_knight_id IS NOT NULL
                GROUP BY d.owner_knight_id
                ORDER BY COUNT(*) DESC
            """)
            knight_rows = cur.fetchall()
            sections.append("## Per-Knight Breakdown\n")
            if knight_rows:
                sections.append("| Knight | Deals | Value | Last Action |")
                sections.append("|---|---|---|---|")
                for kid, count, value, last_action in knight_rows:
                    last = last_action.strftime("%m/%d") if last_action else "never"
                    sections.append(f"| {kid} | {count} | ${value/100:,.0f} | {last} |")
            else:
                sections.append("*No knight-owned deals.*")

    except PipelineStatusDataSourceError:
        raise
    except Exception as exc:
        raise PipelineStatusDataSourceError(f"Query failed: {exc}") from exc
    finally:
        conn.close()

    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pipeline status report")
    parser.add_argument("--dry-run", action="store_true", help="Print report, don't post to Discord")
    args = parser.parse_args()

    db_url = os.environ.get("MIRROR_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: MIRROR_DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    report = generate_report(db_url)

    if args.dry_run:
        print(report)
        return

    # Post to Discord #mumega-pipeline
    channel_id = os.environ.get("DISCORD_PIPELINE_CHANNEL_ID", "")
    if not channel_id:
        print("WARNING: DISCORD_PIPELINE_CHANNEL_ID not set; printing to stdout", file=sys.stderr)
        print(report)
        return

    # Use Discord MCP or direct API
    try:
        from pathlib import Path
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bus_send", Path.home() / "scripts" / "bus-send.py"
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.send(to="loom", text=f"[pipeline-status]\n{report}", source="pipeline-status")
            print("Report delivered to Loom via bus.")
    except Exception as exc:
        raise PipelineStatusDeliveryError(f"Discord delivery failed: {exc}") from exc


if __name__ == "__main__":
    main()
