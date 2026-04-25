"""Sprint telemetry — measurable sprint output without new schema.

Emits sprint_start / sprint_close as audit events; rolls up bus-time + gates +
tests + migrations + commits + production saves from the existing audit_events
stream + git + filesystem. Replaces "engineer-days" with substrate-shipping
units (per Hadi 2026-04-25 calibration directive).

The current sprint slug is stored in /home/mumega/SOS/.current_sprint (single
line). Sprint markers emit to audit_events with stream_id='kernel' and
action='sprint_marker'. Roll-up is a SQL query bounded by start/close timestamps.

No new migrations. Composes with G19 audit chain hardening (signed events).
"""
from __future__ import annotations

import os
import json
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CURRENT_SPRINT_FILE = Path("/home/mumega/SOS/.current_sprint")
SOS_REPO = Path("/home/mumega/SOS")
MUMEGA_REPO = Path("/home/mumega/mumega.com")
MIRROR_REPO = Path("/home/mumega/mirror")


def current_sprint() -> str | None:
    """Return active sprint slug, or None if no sprint is open."""
    if not CURRENT_SPRINT_FILE.exists():
        return None
    val = CURRENT_SPRINT_FILE.read_text().strip()
    return val if val else None


def _set_current_sprint(slug: str | None) -> None:
    if slug is None:
        CURRENT_SPRINT_FILE.unlink(missing_ok=True)
        return
    CURRENT_SPRINT_FILE.write_text(slug + "\n")


def sprint_start(sprint_id: str, opened_by: str = "loom") -> dict[str, Any]:
    """Mark sprint open. Emits an audit event + sets the current-sprint pointer.

    Returns the marker payload. Caller responsible for actually emitting via
    audit_chain.audit_emit() if connected to the kernel; this function only
    records to the local pointer file when audit_chain is unavailable (e.g.
    for retro reconstruction of past sprints).
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "sprint_id": sprint_id,
        "action": "start",
        "opened_by": opened_by,
        "ts": ts,
    }
    _set_current_sprint(sprint_id)
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="kernel",
            actor_id=opened_by,
            actor_type="agent",
            action="sprint_marker",
            resource=f"sprint:{sprint_id}",
            payload=payload,
        )
    except Exception:
        # Fall back to local marker file when kernel unavailable
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"{sprint_id}.start.json").write_text(json.dumps(payload, indent=2))
    return payload


def sprint_close(sprint_id: str, closed_by: str = "loom", note: str | None = None) -> dict[str, Any]:
    """Mark sprint closed. Emits audit event + clears current-sprint pointer."""
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "sprint_id": sprint_id,
        "action": "close",
        "closed_by": closed_by,
        "ts": ts,
        "note": note,
    }
    if current_sprint() == sprint_id:
        _set_current_sprint(None)
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="kernel",
            actor_id=closed_by,
            actor_type="agent",
            action="sprint_marker",
            resource=f"sprint:{sprint_id}",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"{sprint_id}.close.json").write_text(json.dumps(payload, indent=2))
    return payload


@dataclass
class SprintStats:
    """Measurable substrate output for a single sprint."""
    sprint_id: str
    started_at: str | None
    closed_at: str | None
    bus_time_seconds: int | None
    gates_closed: int
    tests_added: int  # net new test files in window
    migrations_applied: int  # new migration files in window
    contract_files_shipped: int  # net new files in sos/contracts/ in window
    commits_landed: int  # across SOS, mumega.com, mirror
    audit_events_emitted: int
    production_saves: int  # bus messages from athena flagging incident-and-fix
    adversarial_findings: int | None  # set if adversarial review run

    def to_markdown(self) -> str:
        lines = [
            f"## Sprint {self.sprint_id} stats",
            "",
            f"- **Bus time:** {self.bus_time_seconds // 60 if self.bus_time_seconds else 'n/a'} min ({self.bus_time_seconds}s raw)",
            f"- **Gates closed:** {self.gates_closed}",
            f"- **Tests added:** {self.tests_added}",
            f"- **Migrations applied:** {self.migrations_applied}",
            f"- **Contract files shipped:** {self.contract_files_shipped}",
            f"- **Commits landed:** {self.commits_landed}",
            f"- **Audit events emitted:** {self.audit_events_emitted}",
            f"- **Production saves required:** {self.production_saves}",
        ]
        if self.adversarial_findings is not None:
            lines.append(f"- **Adversarial findings:** {self.adversarial_findings}")
        return "\n".join(lines)


def compute_sprint_stats(sprint_id: str, db_url: str | None = None) -> SprintStats:
    """Compute measurable output for a sprint by rolling up audit_events + git + fs.

    Reads start/close markers from audit_events (or .sprint_markers/ fallback),
    bounds query window, counts gates / tests / migrations / commits / saves.
    """
    started_at, closed_at = _read_sprint_markers(sprint_id, db_url=db_url)
    bus_time_seconds = None
    if started_at and closed_at:
        try:
            t0 = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            bus_time_seconds = int((t1 - t0).total_seconds())
        except Exception:
            pass

    gates_closed = _count_audit_actions(db_url, sprint_id, ["gate_passed", "gate_green"], started_at, closed_at)
    audit_events_emitted = _count_audit_events(db_url, started_at, closed_at)
    production_saves = _count_audit_actions(db_url, sprint_id, ["incident_resolved", "production_save"], started_at, closed_at)

    tests_added = _count_files_in_window("tests/", started_at, closed_at, repos=[SOS_REPO])
    migrations_applied = _count_files_in_window("migrations/", started_at, closed_at, repos=[MIRROR_REPO])
    contract_files_shipped = _count_files_in_window("sos/contracts/", started_at, closed_at, repos=[SOS_REPO])
    commits_landed = _count_commits_in_window(started_at, closed_at)

    return SprintStats(
        sprint_id=sprint_id,
        started_at=started_at,
        closed_at=closed_at,
        bus_time_seconds=bus_time_seconds,
        gates_closed=gates_closed,
        tests_added=tests_added,
        migrations_applied=migrations_applied,
        contract_files_shipped=contract_files_shipped,
        commits_landed=commits_landed,
        audit_events_emitted=audit_events_emitted,
        production_saves=production_saves,
        adversarial_findings=None,  # caller fills in from adversarial report if applicable
    )


# ----- helpers -----

def _read_sprint_markers(sprint_id: str, db_url: str | None = None) -> tuple[str | None, str | None]:
    """Return (start_ts, close_ts). Prefer audit_events; fall back to local markers."""
    started, closed = None, None
    if db_url:
        try:
            import psycopg2
            with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT payload->>'action', payload->>'ts'
                    FROM audit_events
                    WHERE action = 'sprint_marker'
                      AND resource = %s
                    ORDER BY ts ASC
                    """,
                    (f"sprint:{sprint_id}",),
                )
                for action, ts in cur.fetchall():
                    if action == "start":
                        started = ts
                    elif action == "close":
                        closed = ts
        except Exception:
            pass
    if not started or not closed:
        marker_dir = SOS_REPO / ".sprint_markers"
        if marker_dir.exists():
            start_p = marker_dir / f"{sprint_id}.start.json"
            close_p = marker_dir / f"{sprint_id}.close.json"
            if start_p.exists():
                started = json.loads(start_p.read_text()).get("ts")
            if close_p.exists():
                closed = json.loads(close_p.read_text()).get("ts")
    return started, closed


def _count_audit_events(db_url: str | None, start: str | None, end: str | None) -> int:
    if not (db_url and start and end):
        return 0
    try:
        import psycopg2
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM audit_events WHERE ts >= %s AND ts <= %s",
                (start, end),
            )
            (n,) = cur.fetchone()
            return int(n)
    except Exception:
        return 0


def _count_audit_actions(db_url: str | None, sprint_id: str, actions: list[str], start: str | None, end: str | None) -> int:
    if not (db_url and start and end):
        return 0
    try:
        import psycopg2
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action = ANY(%s) AND ts >= %s AND ts <= %s",
                (actions, start, end),
            )
            (n,) = cur.fetchone()
            return int(n)
    except Exception:
        return 0


def _count_files_in_window(subpath: str, start: str | None, end: str | None, *, repos: list[Path]) -> int:
    """Count net new files added under subpath between two timestamps via git log."""
    if not (start and end):
        return 0
    total = 0
    for repo in repos:
        try:
            out = subprocess.check_output(
                ["git", "-C", str(repo), "log", f"--since={start}", f"--until={end}",
                 "--name-status", "--pretty=format:"],
                stderr=subprocess.DEVNULL,
            ).decode()
            for line in out.splitlines():
                if line.startswith("A\t") and subpath in line:
                    total += 1
        except Exception:
            continue
    return total


def _count_commits_in_window(start: str | None, end: str | None) -> int:
    if not (start and end):
        return 0
    total = 0
    for repo in (SOS_REPO, MUMEGA_REPO, MIRROR_REPO):
        try:
            out = subprocess.check_output(
                ["git", "-C", str(repo), "log", f"--since={start}", f"--until={end}", "--oneline"],
                stderr=subprocess.DEVNULL,
            ).decode()
            total += len([ln for ln in out.splitlines() if ln.strip()])
        except Exception:
            continue
    return total


# ----- CLI for ad-hoc retro use -----

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Sprint telemetry CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_open = sub.add_parser("start", help="Mark a sprint open")
    s_open.add_argument("sprint_id")
    s_open.add_argument("--by", default="loom")

    s_close = sub.add_parser("close", help="Mark a sprint closed")
    s_close.add_argument("sprint_id")
    s_close.add_argument("--by", default="loom")
    s_close.add_argument("--note", default=None)

    s_stats = sub.add_parser("stats", help="Roll up sprint stats")
    s_stats.add_argument("sprint_id")
    s_stats.add_argument("--db", default=os.environ.get("MIRROR_DATABASE_URL"))

    s_now = sub.add_parser("current", help="Print active sprint slug")

    args = p.parse_args()
    if args.cmd == "start":
        print(json.dumps(sprint_start(args.sprint_id, opened_by=args.by), indent=2))
    elif args.cmd == "close":
        print(json.dumps(sprint_close(args.sprint_id, closed_by=args.by, note=args.note), indent=2))
    elif args.cmd == "stats":
        stats = compute_sprint_stats(args.sprint_id, db_url=args.db)
        print(stats.to_markdown())
        print()
        print(json.dumps(asdict(stats), indent=2, default=str))
    elif args.cmd == "current":
        print(current_sprint() or "(no active sprint)")
