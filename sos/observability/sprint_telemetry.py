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
    # For open sprints, use now() as the query window end so live counts are non-zero.
    query_end = closed_at or (datetime.now(timezone.utc).isoformat() if started_at else None)
    bus_time_seconds = None
    if started_at and closed_at:
        try:
            t0 = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            bus_time_seconds = int((t1 - t0).total_seconds())
        except Exception:
            pass

    gates_closed = _count_audit_actions(db_url, sprint_id, ["gate_verdict", "gate_passed", "gate_green"], started_at, query_end)
    audit_events_emitted = _count_audit_events(db_url, started_at, query_end)
    production_saves = _count_audit_actions(db_url, sprint_id, ["incident_resolved", "production_save"], started_at, query_end)
    adversarial_findings_count = _count_audit_actions(db_url, sprint_id, ["adversarial_finding"], started_at, query_end)

    tests_added = _count_files_in_window("tests/", started_at, query_end, repos=[SOS_REPO])
    migrations_applied = _count_files_in_window("migrations/", started_at, query_end, repos=[MIRROR_REPO])
    contract_files_shipped = _count_files_in_window("sos/contracts/", started_at, query_end, repos=[SOS_REPO])
    commits_landed = _count_commits_in_window(started_at, query_end)

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
        adversarial_findings=adversarial_findings_count or None,  # None if no DB; caller may override
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


# ----- emit helpers for gate verdicts, incidents, adversarial findings -----

def emit_gate_verdict(gate_id: str, verdict: str, emitted_by: str = "athena") -> dict[str, Any]:
    """Emit a gate_verdict audit event. Verdict should be GREEN/YELLOW/BLOCKED/RESHAPE."""
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"gate_id": gate_id, "verdict": verdict.upper(), "emitted_by": emitted_by, "ts": ts}
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="kernel",
            actor_id=emitted_by,
            actor_type="agent",
            action="gate_verdict",
            resource=f"gate:{gate_id}",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"gate_verdict_{gate_id}_{ts[:19].replace(':', '-')}.json").write_text(
            json.dumps(payload, indent=2)
        )
    return payload


def emit_incident_resolved(description: str, emitted_by: str = "athena") -> dict[str, Any]:
    """Emit a production_save audit event for sprint telemetry."""
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"description": description, "emitted_by": emitted_by, "ts": ts}
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="kernel",
            actor_id=emitted_by,
            actor_type="agent",
            action="incident_resolved",
            resource="production:save",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"incident_resolved_{ts[:19].replace(':', '-')}.json").write_text(
            json.dumps(payload, indent=2)
        )
    return payload


def emit_adversarial_finding(finding_id: str, severity: str, emitted_by: str = "athena") -> dict[str, Any]:
    """Emit an adversarial_finding audit event for sprint telemetry."""
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"finding_id": finding_id, "severity": severity.upper(), "emitted_by": emitted_by, "ts": ts}
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="kernel",
            actor_id=emitted_by,
            actor_type="agent",
            action="adversarial_finding",
            resource=f"finding:{finding_id}",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"adversarial_finding_{finding_id}_{ts[:19].replace(':', '-')}.json").write_text(
            json.dumps(payload, indent=2)
        )
    return payload


def emit_leader_election(
    instance_id: str,
    role: str,
    transition_reason: str,
    emitted_by: str = "matchmaker",
) -> dict[str, Any]:
    """Emit a leader_election audit event for matchmaker dual-instance HA (Sprint 006 A.1 / G50).

    role: 'leader' | 'observer'
    transition_reason: 'startup_acquired' | 'startup_observer' | 'failover_acquired' |
                       'lost_lock_demoted' | 'shutdown'
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "instance_id": instance_id,
        "role": role,
        "transition_reason": transition_reason,
        "emitted_by": emitted_by,
        "ts": ts,
    }
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="matchmaker",
            actor_id=emitted_by,
            actor_type="service",
            action="leader_election",
            resource=f"instance:{instance_id}",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"leader_election_{instance_id}_{ts[:19].replace(':', '-')}.json").write_text(
            json.dumps(payload, indent=2)
        )
    return payload


def emit_mirror_health(
    instance_id: str,
    prev_status: str,
    new_status: str,
    db_reachable_ms: float,
    emitted_by: str = "mirror",
) -> dict[str, Any]:
    """Emit a mirror_health_transition event — fires only on status change (Sprint 006 A.2 / G71).

    prev_status / new_status: 'healthy' | 'unhealthy'
    Transition-only: callers must only invoke when prev_status != new_status.
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "instance_id": instance_id,
        "prev_status": prev_status,
        "new_status": new_status,
        "db_reachable_ms": db_reachable_ms,
        "emitted_by": emitted_by,
        "ts": ts,
    }
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="mirror",
            actor_id=emitted_by,
            actor_type="service",
            action="mirror_health_transition",
            resource=f"instance:{instance_id}",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"mirror_health_{instance_id}_{ts[:19].replace(':', '-')}.json").write_text(
            json.dumps(payload, indent=2)
        )
    return payload


def emit_squad_health(
    instance_id: str,
    prev_status: str,
    new_status: str,
    db_reachable_ms: float,
    emitted_by: str = "squad",
) -> dict[str, Any]:
    """Emit a squad_health_transition event — fires only on status change (Sprint 006 A.3 / G72).

    prev_status / new_status: 'healthy' | 'unhealthy'
    Transition-only: callers must only invoke when prev_status != new_status.
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "instance_id": instance_id,
        "prev_status": prev_status,
        "new_status": new_status,
        "db_reachable_ms": db_reachable_ms,
        "emitted_by": emitted_by,
        "ts": ts,
    }
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="squad",
            actor_id=emitted_by,
            actor_type="service",
            action="squad_health_transition",
            resource=f"instance:{instance_id}",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"squad_health_{instance_id}_{ts[:19].replace(':', '-')}.json").write_text(
            json.dumps(payload, indent=2)
        )
    return payload


def emit_claim_ttl_reclaim(
    task_id: str,
    prior_owner_pid: int | None,
    new_owner_pid: int,
    age_seconds: float,
    emitted_by: str = "squad",
) -> dict[str, Any]:
    """Emit a claim_ttl_reclaim event when a TTL-expired task is re-claimed (Sprint 006 A.3 / G72).

    Tracks how often the TTL primitive fires — signal for tuning CLAIM_TTL_SECONDS.
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "task_id": task_id,
        "prior_owner_pid": prior_owner_pid,
        "new_owner_pid": new_owner_pid,
        "age_seconds": age_seconds,
        "emitted_by": emitted_by,
        "ts": ts,
    }
    try:
        from sos.kernel.audit_chain import audit_emit  # type: ignore
        audit_emit(
            stream_id="squad",
            actor_id=emitted_by,
            actor_type="service",
            action="claim_ttl_reclaim",
            resource=f"task:{task_id}",
            payload=payload,
        )
    except Exception:
        marker_dir = SOS_REPO / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        (marker_dir / f"claim_ttl_reclaim_{task_id}_{ts[:19].replace(':', '-')}.json").write_text(
            json.dumps(payload, indent=2)
        )
    return payload


def drain_sprint_markers(dry_run: bool = False) -> dict[str, Any]:
    """C.6: Drain .sprint_markers/*.json into audit_events when DB reachable.

    Scans .sprint_markers/ for unprocessed JSON files, writes each as a row
    in audit_events via direct psycopg2 INSERT (bypasses the async audit_chain
    layer — appropriate for a one-shot batch drain), then moves successfully
    ingested files to .sprint_markers/ingested/ to prevent double-emit.

    Returns a summary: {drained, skipped, failed, dry_run, db_reachable}.
    """
    import psycopg2
    import psycopg2.extras

    marker_dir = SOS_REPO / ".sprint_markers"
    ingested_dir = marker_dir / "ingested"

    if not marker_dir.exists():
        return {"drained": 0, "skipped": 0, "failed": 0, "dry_run": dry_run, "db_reachable": False}

    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(SOS_REPO / ".env")
    except Exception:
        pass
    db_url = os.environ.get("MIRROR_DATABASE_URL") or os.environ.get("DATABASE_URL")
    db_reachable = False
    conn = None
    if not dry_run:
        try:
            conn = psycopg2.connect(db_url)
            db_reachable = True
        except Exception:
            db_reachable = False

    drained, skipped, failed = 0, 0, 0
    results: list[dict] = []

    for f in sorted(marker_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            skipped += 1
            results.append({"file": f.name, "status": "skip-parse-error"})
            continue

        # Resolve audit_events row fields from marker content
        if "gate_id" in data:
            action = "gate_verdict"
            resource = f"gate:{data['gate_id']}"
        elif "finding_id" in data:
            action = "adversarial_finding"
            resource = f"finding:{data['finding_id']}"
        elif "description" in data and "incident" in f.name:
            action = "incident_resolved"
            resource = "production:save"
        elif data.get("action") in ("start", "close") and "sprint_id" in data:
            action = f"sprint_{data['action']}"
            resource = f"sprint:{data['sprint_id']}"
        else:
            skipped += 1
            results.append({"file": f.name, "status": "skip-unknown-type"})
            continue

        actor_id = (
            data.get("emitted_by") or data.get("opened_by")
            or data.get("closed_by") or "athena"
        )

        if dry_run:
            drained += 1
            results.append({"file": f.name, "status": "dry-run", "action": action})
            continue

        if not db_reachable:
            skipped += 1
            results.append({"file": f.name, "status": "skip-db-unreachable"})
            continue

        try:
            import hashlib
            import uuid as _uuid
            from datetime import timezone as _tz
            with conn.cursor() as cur:  # type: ignore[union-attr]
                # Allocate seq atomically (advisory lock inside PG function)
                cur.execute("SELECT audit_next_seq(%s)", ("kernel",))
                seq = cur.fetchone()[0]
                # Fetch prev_hash for chain integrity
                cur.execute(
                    "SELECT hash FROM audit_events WHERE stream_id=%s AND seq=%s",
                    ("kernel", seq - 1),
                )
                prev_row = cur.fetchone()
                prev_hash: bytes | None = prev_row[0] if prev_row else None
                ts_now = datetime.now(_tz.utc)
                event_id = str(_uuid.uuid4())
                # Canonical representation (matches audit_chain.py emit_audit exactly)
                canonical_obj = {
                    "id": event_id,
                    "stream_id": "kernel",
                    "seq": seq,
                    "ts": ts_now.isoformat(),
                    "actor_id": actor_id,
                    "actor_type": "agent",
                    "action": action,
                    "resource": resource,
                    "payload": data,
                    "payload_redacted": False,
                    "prev_hash": prev_hash.hex() if prev_hash else None,
                }
                canonical_bytes = json.dumps(
                    canonical_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
                h = hashlib.sha256()
                if prev_hash:
                    h.update(prev_hash)
                h.update(canonical_bytes)
                event_hash = h.digest()
                cur.execute(
                    """
                    INSERT INTO audit_events
                        (id, stream_id, seq, ts,
                         actor_id, actor_type, action, resource,
                         payload, payload_redacted, prev_hash, hash)
                    VALUES (%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s)
                    """,
                    (
                        event_id, "kernel", seq, ts_now,
                        actor_id, "agent", action, resource,
                        psycopg2.extras.Json(data), False, prev_hash, event_hash,
                    ),
                )
            conn.commit()  # type: ignore[union-attr]
            ingested_dir.mkdir(exist_ok=True)
            f.rename(ingested_dir / f.name)
            drained += 1
            results.append({"file": f.name, "status": "drained", "action": action})
        except Exception as exc:
            try:
                conn.rollback()  # type: ignore[union-attr]
            except Exception:
                pass
            failed += 1
            results.append({"file": f.name, "status": "failed", "error": str(exc)})

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "drained": drained,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "db_reachable": db_reachable,
        "results": results,
    }


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

    s_gate = sub.add_parser("gate-verdict", help="Emit gate_verdict audit event")
    s_gate.add_argument("gate_id", help="e.g. G17b")
    s_gate.add_argument("verdict", help="GREEN | YELLOW | BLOCKED | RESHAPE")
    s_gate.add_argument("--by", default="athena")

    s_incident = sub.add_parser("incident-resolved", help="Emit incident_resolved audit event")
    s_incident.add_argument("description")
    s_incident.add_argument("--by", default="athena")

    s_adv = sub.add_parser("adversarial-finding", help="Emit adversarial_finding audit event")
    s_adv.add_argument("finding_id", help="e.g. F-02")
    s_adv.add_argument("severity", help="BLOCK | HIGH | WARN | LOW")
    s_adv.add_argument("--by", default="athena")

    s_drain = sub.add_parser("drain-markers", help="C.6: drain .sprint_markers/ into audit_events")
    s_drain.add_argument("--dry-run", action="store_true", help="Show what would be drained without emitting")

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
    elif args.cmd == "gate-verdict":
        print(json.dumps(emit_gate_verdict(args.gate_id, args.verdict, emitted_by=args.by), indent=2))
    elif args.cmd == "incident-resolved":
        print(json.dumps(emit_incident_resolved(args.description, emitted_by=args.by), indent=2))
    elif args.cmd == "adversarial-finding":
        print(json.dumps(emit_adversarial_finding(args.finding_id, args.severity, emitted_by=args.by), indent=2))
    elif args.cmd == "drain-markers":
        result = drain_sprint_markers(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        if not args.dry_run:
            print(f"\nDrained {result['drained']} markers, {result['skipped']} skipped, {result['failed']} failed.")
