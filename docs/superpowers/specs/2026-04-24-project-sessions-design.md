# Project Sessions Design Spec
**Date:** 2026-04-24  
**Author:** Kasra  
**Gate:** Athena  
**PM:** Loom  
**Status:** Draft — pending Athena approval before implementation

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

**Wire level (fast, stateless):** Bus token carries `project_id` scope — already established by DISP-001. Request-time check: token's project scope must include the target project_id.

**Role model (human-readable, drives token issuance):** `project_members` table with roles:
- `owner` — full read/write, can add/remove members, receives owner token at mint
- `member` — read/write tasks and sessions for this project
- `observer` — read-only (human stakeholder tokens)

Role check happens **at token issuance only**, not at request time. Token carries the encoded scope; project_members is the audit trail and the source for re-issuance.

**Squad inheritance is explicitly rejected.** A squad serves multiple projects — inheriting squad membership would leak access across project boundaries.

---

## Session Lifecycle

### Check-in (explicit)

`POST /projects/{project_id}/checkin`  
Body: `{ "agent_id": "kasra", "context": {} }`

- Creates `project_sessions` row with `opened_at = now`.
- Emits `checkin` event to `project_session_events`.
- If an open session already exists for this agent+project, returns it (idempotent).
- Response: `{ "session_id": "...", "opened_at": "..." }`

### Heartbeat

`POST /sessions/{session_id}/heartbeat`

- Emits `heartbeat` event. Used by idle-timeout logic.
- No response body needed beyond 200.

### Check-out (explicit)

`POST /sessions/{session_id}/checkout`  
Body: `{ "reason": "done" }` (optional)

- Sets `closed_at`, `close_reason = 'explicit'`.
- Emits `checkout` event.

### Idle auto-close (inferred fallback)

A background job (or lazy check on next checkin) closes sessions where:
- No `heartbeat`, `task_claim`, or `task_complete` event in the last 30 minutes.
- Sets `close_reason = 'idle_timeout'`.

### Inferred check-in

When `claim_task()` runs and the task has a `project` field, if no open session exists for this agent+project, one is opened automatically with `close_reason` left null (will be `idle_timeout` on auto-close).

---

## Human Engagement Tracking

Human messages arrive via Discord/Telegram bot events (already wired) and are emitted as `human_msg` events into `project_session_events`.

**`first_human_response_ms`**: time from `opened_at` to the first `human_msg` event in the session. Written once, on first `human_msg` insert.

**`active_engagement_ms`**: cumulative sum of active windows. An active window is the time between a `human_msg` event and the next `agent_msg` or `task_complete` event, capped at 10 minutes (600,000ms) per window. Computed at checkout or on-demand via SUM query over event pairs.

---

## API Surface

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/projects/{id}/checkin` | member token | Open or resume session |
| POST | `/sessions/{id}/checkout` | member token | Close session |
| POST | `/sessions/{id}/heartbeat` | member token | Keep-alive |
| GET | `/projects/{id}/sessions` | observer token | List sessions (paginated) |
| GET | `/sessions/{id}` | observer token | Session detail + events |
| POST | `/projects/{id}/members` | owner token | Add member with role |
| GET | `/projects/{id}/members` | observer token | List members |
| DELETE | `/projects/{id}/members/{agent_id}` | owner token | Remove member |

---

## Implementation Scope

**Alembic migrations (Squad Service):**
- `0008_project_sessions.py` — create project_sessions, project_session_events, project_members
- `0009_squad_transactions_session_id.py` — ALTER TABLE squad_transactions ADD COLUMN session_id

**Service layer:**
- `sos/services/squad/sessions.py` (new) — `ProjectSessionService`: checkin, checkout, heartbeat, idle-close, human_msg event, active_engagement_ms computation
- `sos/services/squad/members.py` (new) — `ProjectMemberService`: add, remove, list, role check for token issuance

**HTTP layer:**
- `sos/services/squad/app.py` — add 8 routes above

**Sovereign integration:**
- `sovereign/loop.py` — call `/projects/{id}/checkin` before first task claim in a project; `/sessions/{id}/heartbeat` each cycle; `/sessions/{id}/checkout` when task queue for project is empty

**Tests:**
- `tests/test_project_sessions.py` — 6 tests:
  - checkin creates session
  - checkin is idempotent (second call returns same session)
  - checkout closes session
  - idle_timeout auto-close (mock time)
  - inferred checkin on task claim
  - session_id FK appears on squad_transaction after task complete

---

## Implementation Order

1. Migrations 0008 + 0009
2. `sessions.py` service + `members.py` service
3. HTTP routes in app.py
4. Tests
5. Sovereign wiring (loop.py)

Each shipped as one PR, Athena gate before coding.

---

## Out of Scope

- Session replay or event streaming (future)
- Per-session memory snapshots (Mirror integration — future)
- Billing against session time (wallet charges today are per-task, not per-session)
