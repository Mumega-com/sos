# Project Sessions Design Spec
**Date:** 2026-04-24  
**Author:** Kasra  
**Gate:** Athena  
**PM:** Loom  
**Status:** Draft v2 — addressing Athena block items 1–2, concerns 3–5

---

## Summary

Add project check-in/check-out, human engagement tracking, and access control to the Squad Service. This gates customer onboarding — without it we cannot track per-customer token burn, scope access per project, or measure human engagement time.

Lives entirely in Squad Service (same SQLite DB). No new service. Sessions are task-adjacent; separating them would add cross-service calls on every claim/complete.

---

## Schema (4 changes, no column drops)

### New table: `project_sessions`

```sql
CREATE TABLE project_sessions (
    id                     TEXT PRIMARY KEY,
    project_id             TEXT NOT NULL,
    agent_id               TEXT NOT NULL,
    tenant_id              TEXT NOT NULL DEFAULT 'default',
    opened_at              TEXT NOT NULL,
    closed_at              TEXT,
    close_reason           TEXT,           -- 'explicit' | 'idle_timeout' | 'error'
    first_human_response_ms INTEGER,       -- ms from open to first human message
    active_engagement_ms   INTEGER DEFAULT 0  -- cumulative human active window
);
CREATE INDEX idx_project_sessions_project ON project_sessions(project_id, tenant_id);
CREATE INDEX idx_project_sessions_agent   ON project_sessions(agent_id, opened_at DESC);
```

### New table: `project_session_events`

```sql
CREATE TABLE project_session_events (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES project_sessions(id),
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,   -- checkin | checkout | heartbeat | task_claim |
                                 -- task_complete | human_msg | agent_msg
    actor       TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}'
);
CREATE INDEX idx_pse_session ON project_session_events(session_id, ts DESC);
```

### New table: `project_members`

```sql
CREATE TABLE project_members (
    project_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    role        TEXT NOT NULL DEFAULT 'member',  -- 'owner' | 'member' | 'observer'
    added_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, agent_id, tenant_id)
);
```

### Modified: `squad_transactions`

Add column:

```sql
ALTER TABLE squad_transactions ADD COLUMN session_id TEXT;
```

Session aggregate token cost = `SELECT SUM(billable_cents) FROM squad_transactions WHERE session_id = ?`. No separate table needed.

---

## Access Control

**Wire level (fast, stateless):** Bus tokens carry `project_id` scope and `role` claim — established by DISP-001 + extended for project-member tokens. The token payload includes:

```json
{
  "project": "acme-corp",
  "role": "owner"
}
```

**Request-time role check (option a — chosen):** Routes check the `role` claim from the decoded token at request time. The claim field name is `role`. This is lightweight (decoded token is already in scope) and closes the privilege escalation gap — a `member` token cannot call member-management routes.

Route enforcement:
- `owner` routes: verify `token.role == "owner"`
- `member` routes: verify `token.role in ("owner", "member")` — owners can do everything members can
- `observer` routes: verify `token.role in ("owner", "member", "observer")` — readable by all

**Role model (drives token issuance):** `project_members` table with roles:
- `owner` — full read/write, can add/remove members, receives owner token at mint
- `member` — read/write tasks and sessions for this project
- `observer` — read-only (human stakeholder tokens)

Role check happens **at both token issuance AND request time**. Token issuance sets the role claim. Request-time check enforces it. `project_members` is the audit trail and source for re-issuance.

**Squad inheritance is explicitly rejected.** A squad serves multiple projects — inheriting squad membership would leak access across project boundaries.

---

## Session Lifecycle

### Check-in (explicit — authoritative)

`POST /projects/{project_id}/checkin`  
Body: `{ "agent_id": "kasra", "context": {} }`

- Creates `project_sessions` row with `opened_at = now`.
- Emits `checkin` event to `project_session_events`.
- If an open session already exists for this agent+project, returns it (idempotent).
- Response: `{ "session_id": "...", "opened_at": "..." }`

Sovereign loop calls this before each task claim. This is the **single authoritative** check-in path. `claim_task()` does **not** infer a session — that responsibility belongs to the sovereign loop, which already has the project context and calls `/checkin` explicitly.

### Heartbeat

`POST /sessions/{session_id}/heartbeat`

- Emits `heartbeat` event. Used by idle-timeout logic.
- No response body needed beyond 200.

### Check-out (explicit)

`POST /sessions/{session_id}/checkout`  
Body: `{ "reason": "done" }` (optional)

- Sets `closed_at`, `close_reason = 'explicit'`.
- Emits `checkout` event.

### Idle auto-close (two-path approach — both committed)

**Path 1 — Lazy check on checkin:** When `POST /projects/{id}/checkin` is called and an existing open session is found, the service checks if it is idle (no `heartbeat`, `task_claim`, or `task_complete` event in last 30 minutes). If idle, it closes the stale session before opening a new one. This catches the common "agent reconnects after a break" case.

**Path 2 — Sovereign loop sweep:** At the top of each loop cycle, sovereign calls `close_idle_sessions(project_id, cutoff=now()-30min)` before claiming. This catches abandoned sessions where the agent never reconnects. One call, no new scheduler — reuses the existing loop.

Both paths set `close_reason = 'idle_timeout'`. Both are safe to run concurrently (first writer wins on the UPDATE; second is a no-op on an already-closed session).

---

## Human Engagement Tracking

Human messages arrive via Discord/Telegram bot events (already wired) and are emitted as `human_msg` events into `project_session_events`.

**`first_human_response_ms`**: time from `opened_at` to the first `human_msg` event in the session. Written once, on first `human_msg` insert.

**`active_engagement_ms`**: cumulative sum of active windows. An active window is the time between a `human_msg` event and the next `agent_msg` or `task_complete` event, capped at 10 minutes (600,000ms) per window.

**Computation SQL (SQLite — computed at checkout or on-demand):**

```sql
WITH human_msgs AS (
    SELECT ts AS human_ts
    FROM project_session_events
    WHERE session_id = :session_id AND kind = 'human_msg'
),
paired AS (
    -- For each human_msg, find the next agent_msg or task_complete by timestamp
    SELECT hm.human_ts,
           MIN(ae.ts) AS close_ts
    FROM human_msgs hm
    JOIN project_session_events ae
        ON ae.session_id = :session_id
        AND ae.ts > hm.human_ts
        AND ae.kind IN ('agent_msg', 'task_complete')
    GROUP BY hm.human_ts
),
windows AS (
    SELECT MIN(
        CAST((julianday(close_ts) - julianday(human_ts)) * 86400000 AS INTEGER),
        600000
    ) AS window_ms
    FROM paired
)
SELECT COALESCE(SUM(window_ms), 0) AS active_engagement_ms
FROM windows;
```

**Example:** Session has events at:
- T+0s: `checkin` (agent)
- T+60s: `human_msg` (human)
- T+90s: `agent_msg` (agent)     ← window 1: 30s
- T+200s: `human_msg` (human)
- T+900s: `task_complete` (agent) ← window 2: 700s, capped to 600s
- `active_engagement_ms = 30,000 + 600,000 = 630,000`

---

## API Surface

| Method | Path | Required token role | Description |
|--------|------|---------------------|-------------|
| POST | `/projects/{id}/checkin` | member | Open or resume session |
| POST | `/sessions/{id}/checkout` | member | Close session |
| POST | `/sessions/{id}/heartbeat` | member | Keep-alive |
| GET | `/projects/{id}/sessions` | observer | List sessions (paginated) |
| GET | `/sessions/{id}` | observer | Session detail + events |
| POST | `/projects/{id}/members` | owner | Add member with role |
| GET | `/projects/{id}/members` | observer | List members |
| DELETE | `/projects/{id}/members/{agent_id}` | owner | Remove member |

Role enforcement at request time (as per Access Control section above). `owner` tokens satisfy `member` and `observer` routes. `member` tokens satisfy `observer` routes.

---

## Implementation Scope

**Alembic migrations (Squad Service):**
- `0008_project_sessions.py` — create project_sessions, project_session_events, project_members
- `0009_squad_transactions_session_id.py` — ALTER TABLE squad_transactions ADD COLUMN session_id

**Service layer:**
- `sos/services/squad/sessions.py` (new) — `ProjectSessionService`: checkin, checkout, heartbeat, idle-close (both paths), human_msg event, active_engagement_ms computation (SQL above)
- `sos/services/squad/members.py` (new) — `ProjectMemberService`: add, remove, list, role check for token issuance

**HTTP layer:**
- `sos/services/squad/app.py` — add 8 routes above with request-time role check via `token.role` claim

**Sovereign integration:**
- `sovereign/loop.py` — call `/projects/{id}/checkin` before first task claim in a project; `/sessions/{id}/heartbeat` each cycle; `/sessions/{id}/checkout` when task queue for project is empty; `close_idle_sessions(project_id)` at top of each cycle
- **Remove:** any session inference from `claim_task()` — sovereign loop is the authoritative check-in path

**Tests:**
- `tests/test_project_sessions.py` — 9 tests:
  - checkin creates session
  - checkin is idempotent (second call returns same session)
  - checkout closes session
  - idle_timeout auto-close on checkin (lazy path, mock time)
  - idle_timeout auto-close on loop sweep (sovereign path, mock time)
  - member add with role, member remove
  - member token → 403 on owner-only route
  - project-scoped token → 403 on different project_id
  - `active_engagement_ms` math (30s window + 700s capped to 600s = 630,000ms)

---

## Implementation Order

1. Migrations 0008 + 0009
2. `sessions.py` service + `members.py` service
3. HTTP routes in app.py (with role enforcement)
4. Tests (all 9)
5. Sovereign wiring (loop.py — add sweep, add explicit checkin, remove claim_task inference)

Each shipped as one PR, Athena gate before coding.

---

## Out of Scope

- Session replay or event streaming (future)
- Per-session memory snapshots (Mirror integration — future)
- Billing against session time (wallet charges today are per-task, not per-session)
