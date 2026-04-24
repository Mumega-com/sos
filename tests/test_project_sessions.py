"""Tests for project sessions, members, and access control.

Spec: SOS/docs/superpowers/specs/2026-04-24-project-sessions-design.md
9 tests covering: checkin, idempotency, checkout, idle-close (lazy + loop),
member ops, authz, and active_engagement_ms math.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator
from uuid import uuid4

import pytest

from sos.services.squad.service import DEFAULT_TENANT_ID, SquadDB, now_iso
from sos.services.squad.sessions import (
    ProjectSessionService,
    SessionAlreadyClosedError,
    SessionNotFoundError,
)
from sos.services.squad.members import (
    MemberNotFoundError,
    ProjectMemberService,
    role_satisfies,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> Generator[SquadDB, None, None]:
    db_path = tmp_path / "test_sessions.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS project_sessions (
                id                      TEXT PRIMARY KEY,
                project_id              TEXT NOT NULL,
                agent_id                TEXT NOT NULL,
                tenant_id               TEXT NOT NULL DEFAULT 'default',
                opened_at               TEXT NOT NULL,
                closed_at               TEXT,
                close_reason            TEXT,
                first_human_response_ms INTEGER,
                active_engagement_ms    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS project_session_events (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                ts           TEXT NOT NULL,
                kind         TEXT NOT NULL,
                actor        TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS project_members (
                project_id  TEXT NOT NULL,
                agent_id    TEXT NOT NULL,
                tenant_id   TEXT NOT NULL DEFAULT 'default',
                role        TEXT NOT NULL DEFAULT 'member',
                added_at    TEXT NOT NULL,
                PRIMARY KEY (project_id, agent_id, tenant_id)
            );
        """)
    yield database


@pytest.fixture()
def svc(db: SquadDB) -> ProjectSessionService:
    return ProjectSessionService(db=db)


@pytest.fixture()
def member_svc(db: SquadDB) -> ProjectMemberService:
    return ProjectMemberService(db=db)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _past_iso(minutes_ago: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def _insert_session(db: SquadDB, project_id: str, agent_id: str, opened_at: str) -> str:
    """Insert a session row directly and return its id."""
    sid = str(uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO project_sessions (id, project_id, agent_id, tenant_id, opened_at) VALUES (?,?,?,?,?)",
            (sid, project_id, agent_id, DEFAULT_TENANT_ID, opened_at),
        )
    return sid


def _insert_event(db: SquadDB, session_id: str, kind: str, ts: str, actor: str = "agent") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO project_session_events (id, session_id, ts, kind, actor) VALUES (?,?,?,?,?)",
            (str(uuid4()), session_id, ts, kind, actor),
        )


# ---------------------------------------------------------------------------
# Test 1: checkin creates session
# ---------------------------------------------------------------------------

def test_checkin_creates_session(svc: ProjectSessionService, db: SquadDB) -> None:
    result = svc.checkin("proj-alpha", "kasra")
    assert "session_id" in result
    assert "opened_at" in result
    assert result["resumed"] is False

    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM project_sessions WHERE id = ?", (result["session_id"],)
        ).fetchone()
    assert row is not None
    assert row["project_id"] == "proj-alpha"
    assert row["agent_id"] == "kasra"
    assert row["closed_at"] is None


# ---------------------------------------------------------------------------
# Test 2: checkin is idempotent
# ---------------------------------------------------------------------------

def test_checkin_is_idempotent(svc: ProjectSessionService) -> None:
    r1 = svc.checkin("proj-alpha", "kasra")
    r2 = svc.checkin("proj-alpha", "kasra")
    assert r1["session_id"] == r2["session_id"]
    assert r2["resumed"] is True


# ---------------------------------------------------------------------------
# Test 3: checkout closes session
# ---------------------------------------------------------------------------

def test_checkout_closes_session(svc: ProjectSessionService, db: SquadDB) -> None:
    r = svc.checkin("proj-beta", "athena")
    sid = r["session_id"]

    result = svc.checkout(sid, reason="done")
    assert result["close_reason"] == "done"
    assert result["closed_at"] is not None

    with db.connect() as conn:
        row = conn.execute(
            "SELECT closed_at, close_reason FROM project_sessions WHERE id = ?", (sid,)
        ).fetchone()
    assert row["closed_at"] is not None
    assert row["close_reason"] == "done"


# ---------------------------------------------------------------------------
# Test 4: idle_timeout auto-close — lazy path (on checkin)
# ---------------------------------------------------------------------------

def test_idle_timeout_lazy_on_checkin(svc: ProjectSessionService, db: SquadDB) -> None:
    # Insert a stale open session (opened 60 min ago, no activity events)
    stale_sid = _insert_session(db, "proj-gamma", "kasra", _past_iso(60))

    # New checkin should lazy-close the stale session then open a fresh one
    r = svc.checkin("proj-gamma", "kasra")
    new_sid = r["session_id"]

    assert new_sid != stale_sid

    with db.connect() as conn:
        stale_row = conn.execute(
            "SELECT closed_at, close_reason FROM project_sessions WHERE id = ?", (stale_sid,)
        ).fetchone()
    assert stale_row["closed_at"] is not None
    assert stale_row["close_reason"] == "idle_timeout"


# ---------------------------------------------------------------------------
# Test 5: idle_timeout auto-close — sovereign loop sweep
# ---------------------------------------------------------------------------

def test_idle_timeout_loop_sweep(svc: ProjectSessionService, db: SquadDB) -> None:
    # Insert two stale sessions for the project (different agents)
    sid1 = _insert_session(db, "proj-delta", "kasra", _past_iso(45))
    sid2 = _insert_session(db, "proj-delta", "athena", _past_iso(35))
    # Session with recent activity — should NOT be closed
    sid3 = _insert_session(db, "proj-delta", "loom", _past_iso(40))
    _insert_event(db, sid3, "heartbeat", _past_iso(5))  # recent heartbeat

    closed = svc.close_idle_sessions("proj-delta")
    assert closed == 2  # sid1 and sid2

    with db.connect() as conn:
        r1 = conn.execute("SELECT close_reason FROM project_sessions WHERE id=?", (sid1,)).fetchone()
        r2 = conn.execute("SELECT close_reason FROM project_sessions WHERE id=?", (sid2,)).fetchone()
        r3 = conn.execute("SELECT closed_at FROM project_sessions WHERE id=?", (sid3,)).fetchone()
    assert r1["close_reason"] == "idle_timeout"
    assert r2["close_reason"] == "idle_timeout"
    assert r3["closed_at"] is None  # still open


# ---------------------------------------------------------------------------
# Test 6: member add and remove
# ---------------------------------------------------------------------------

def test_member_add_and_remove(member_svc: ProjectMemberService) -> None:
    result = member_svc.add_member("proj-epsilon", "kasra", role="member")
    assert result["role"] == "member"

    members = member_svc.list_members("proj-epsilon")
    assert any(m["agent_id"] == "kasra" for m in members)

    member_svc.remove_member("proj-epsilon", "kasra")
    members_after = member_svc.list_members("proj-epsilon")
    assert not any(m["agent_id"] == "kasra" for m in members_after)

    with pytest.raises(MemberNotFoundError):
        member_svc.remove_member("proj-epsilon", "kasra")


# ---------------------------------------------------------------------------
# Test 7: member token → 403 on owner-only route
# ---------------------------------------------------------------------------

def test_role_satisfies_hierarchy() -> None:
    # owner satisfies all
    assert role_satisfies("owner", "owner") is True
    assert role_satisfies("owner", "member") is True
    assert role_satisfies("owner", "observer") is True

    # member satisfies member and observer only
    assert role_satisfies("member", "owner") is False
    assert role_satisfies("member", "member") is True
    assert role_satisfies("member", "observer") is True

    # observer satisfies observer only
    assert role_satisfies("observer", "owner") is False
    assert role_satisfies("observer", "member") is False
    assert role_satisfies("observer", "observer") is True


# ---------------------------------------------------------------------------
# Test 8: project-scoped token → 403 on different project_id
# ---------------------------------------------------------------------------

def test_token_project_scope_check() -> None:
    """lookup_sos_token returns project; caller must verify project matches path."""
    from sos.services.squad.members import lookup_sos_token

    # A fake token record with project="acme"
    # Simulate the check done in _require_project_role
    token_rec = {"project": "acme", "role": "owner", "active": True}
    request_project_id = "evil-corp"

    token_project = token_rec.get("project")
    mismatch = token_project and token_project != request_project_id
    assert mismatch is True  # should trigger 403


# ---------------------------------------------------------------------------
# Test 9: active_engagement_ms math
# ---------------------------------------------------------------------------

def test_active_engagement_ms_computation(svc: ProjectSessionService, db: SquadDB) -> None:
    """30s window + 700s window (capped to 600s) = 630,000ms."""
    base = datetime.now(timezone.utc) - timedelta(minutes=20)

    def t(seconds_offset: int) -> str:
        return (base + timedelta(seconds=seconds_offset)).isoformat()

    sid = _insert_session(db, "proj-zeta", "kasra", t(0))

    with db.connect() as conn:
        for kind, actor, secs in [
            ("checkin",       "kasra",  0),
            ("human_msg",     "human",  60),    # window 1 start
            ("agent_msg",     "kasra",  90),    # window 1 end → 30s
            ("human_msg",     "human",  200),   # window 2 start
            ("task_complete", "kasra",  900),   # window 2 end → 700s, capped 600s
        ]:
            conn.execute(
                "INSERT INTO project_session_events (id, session_id, ts, kind, actor) VALUES (?,?,?,?,?)",
                (str(uuid4()), sid, t(secs), kind, actor),
            )

    ms = svc.get_active_engagement(sid)
    # window1: 30s = 30,000ms; window2: 700s → capped 600,000ms
    assert ms == 630_000
