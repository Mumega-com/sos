# Section 4 — Partner Workspace UI, Chat, and Discord Provisioning

**Author:** Loom
**Date:** 2026-04-24
**Depends on:** Section 1 (role registry + RBAC tokens), Section 2 (evidence ingestion + audit chain), Section 3 (knight lifecycle)
**Gate:** Athena
**Owner:** Kasra

---

## Overview

Section 4 covers the surfaces where humans and agents meet: a role-gated partner workspace in the GAF web app (`/partner` route, Next.js in `gaf-app/web/app/partner/`), a three-party messaging primitive built on Supabase Realtime, and automated Discord channel provisioning wired into the knight-minting ceremony. All three subsystems share the same backing data — one `case_messages` table, one commission ledger, one audit chain — rendered differently per role and per surface. Partners get a web UI; customers get the same chat via `/my-cases/[id]`; agents get the bus. The design constraint throughout: no surface may write data without going through the same API endpoints and forensic audit chain.

---

## 4.1 Partner Workspace UI (`/partner`)

The partner workspace lives at `gaf-app/web/app/partner/` and is a role-gated frontend for humans acting as partners (Noor, Gavin, Lex, Ron, Hossein, future Boast rep, and others). It is a Next.js App Router subtree — server components by default, React islands only where Supabase Realtime or form interactions require client hydration.

### Pages

| Route | Purpose |
|---|---|
| `/partner` | Dashboard: assigned customers list, active chat threads, pending tasks, commission ledger snippet |
| `/partner/customers/[id]` | Per-customer view: scan state, evidence pipeline stage, case status, chat thread, evidence upload, commission earned for this case |
| `/partner/commissions` | Full commission ledger (earned / pending / paid / scheduled payouts) + Stripe Connect status |
| `/partner/kb` | Squad knowledge base (Inkwell squad tier) — read-only |
| `/partner/profile` | Contact record, squad memberships, role assignments |

### Auth and Visibility

- Supabase Auth session validated in `app/partner/layout.tsx` via `createServerClient`.
- Role claim read from the role registry (Section 1); missing claim redirects to `/dashboard`.
- Row-level security on `opportunities`, `cases`, and `case_threads` enforces `partner_id = auth.uid()` — the DB is the last line of visibility, not just the UI.
- Partners see only commission rows where `partner_id = auth.uid()`.

### Design

- Tailwind + shadcn components — matches GAF's existing design system.
- Mobile-responsive; Noor's use case (on phone at events) drives touch target sizing on `/partner/customers/[id]`.
- Empty states use the GAF character mascot from `/web/public/images/brand/`.

---

## 4.2 Chat / Messaging Primitive (Supabase Realtime)

Every case has one persistent thread. Participants: customer, one or more assigned partners, and Kaveh (the project agent). Hadi can join any thread as admin. Athena has read-only audit access at the DB level.

### Schema

```sql
-- Thread container, one per case
CREATE TABLE case_threads (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id      uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  participants jsonb NOT NULL DEFAULT '[]'
  -- participants element: { id: uuid, role: text, joined_at: timestamptz }
);

CREATE INDEX idx_case_threads_case_id ON case_threads(case_id);

-- Individual messages
CREATE TABLE case_messages (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id      uuid NOT NULL REFERENCES case_threads(id) ON DELETE CASCADE,
  sender_id      uuid NOT NULL,
  sender_type    text NOT NULL CHECK (sender_type IN ('human', 'agent')),
  body           text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  forensic_hash  text NOT NULL  -- sha256(body || previous_hash), chained
);

CREATE INDEX idx_case_messages_thread_id ON case_messages(thread_id);

-- RLS: participants only
ALTER TABLE case_messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY case_messages_participants ON case_messages
  FOR ALL USING (
    thread_id IN (
      SELECT id FROM case_threads
      WHERE participants @> jsonb_build_array(
        jsonb_build_object('id', auth.uid()::text)
      )
    )
  );
```

Every `case_messages` row is also written to `forensic_audit_logs` (SOS kernel, Section 2) with hash chaining: `forensic_hash = sha256(body || previous_forensic_hash)`, computed server-side in an insert trigger. This detects any post-insert tampering.

### Realtime Channel

- Channel: `case:{case_id}` via `supabase.channel('case:' + caseId)`.
- Presence used for "Kaveh is typing…" indicator: agent sets `presence.typing = true` for 3 s.
- Clients subscribe on mount, unsubscribe on unmount.

### Agent Integration (Kaveh)

- Kaveh's session subscribes to bus stream `project:gaf:case:*`.
- Inbound human message triggers a bus event; Kaveh calls `POST /cases/{id}/messages` with `sender_type=agent`.
- A bridge service (Hono Worker) relays Supabase Realtime → bus and bus → Supabase Realtime.
- GHL SMS mirror: if case owner opts in, outbound agent/partner messages mirror to SMS via GHL. Replies come back via web only (v1).

### PIPEDA Consent

- Checkbox at case creation: "I consent to chat communications with partners and AI agents." Stored as `consent_chat: true` on the `cases` row.
- `POST /cases/{id}/revoke-consent` sets `consent_chat: false`; chat UI disables compose bar. History retained per CRA 6-year requirement.
- `GET /cases/{id}/thread/export` returns full thread as newline-delimited JSON for customer download.

---

## 4.3 Discord Provisioning (`mint-knight.py --discord-provision`)

The kasra and mumega bot accounts are already on the guild and on the SOS bus. The `--discord-provision` flag on `mint-knight.py` auto-wires a new project into the Discord topology at mint time.

### Channel Taxonomy

| Type | Naming convention | Example | Who reads / writes |
|---|---|---|---|
| Public | `#<topic>` | `#announcements` | All members |
| Role | `#<role>-<scope>` | `#partners-gaf`, `#agents-core` | Role holders |
| Squad | `#squad-<name>` | `#squad-outreach`, `#squad-seo` | Squad members |
| Project | `#project-<customer-slug>` | `#project-metrobit`, `#project-cheffs-kitchen` | Project team + assigned agent |
| Entity | `#entity-<partner-slug>` | `#entity-century21-ron`, `#entity-agentlink` | Partner's team + our side |

### Provisioning Steps

When `--discord-provision` is passed, `mint-knight.py` executes in order:

1. Create `#project-<customer-slug>` channel via Discord REST (`POST /guilds/{guild_id}/channels`).
2. Apply permission overwrites: `Partners-GAF` role can read/write; customer invitees get scoped read access; `Agents` role can read; `@everyone` denied.
3. Add Kaveh bot account to the channel's permission overrides.
4. Post welcome message as Kaveh: _"Hello — I'm the knight for \<customer-name\>. This channel is where we coordinate your Grant & Funding work. A human partner will also be assigned. — Kaveh"_
5. Write `discord_channel_id` back to the Squad Service project record (`PUT /projects/{id}`).
6. Publish `project:gaf:discord:register` bus event with `{project_slug, channel_id}` so the bridge service starts routing.

### Bot Routing Matrix

| Inbound event | Direction | Action |
|---|---|---|
| Human posts in `#project-metrobit` | Discord → bus | Look up `discord_channel_id` → publish to `project:gaf:case:{case_id}:channel:{channel_id}` |
| Kaveh bus reply with Discord metadata | Bus → Discord | Post to `channel_id` from metadata |
| `@kaveh` mention in any project channel | Discord → Kaveh inbox | Resolve → `mcp__sos__send(to="agent:kaveh", ...)` |
| Partner posts in `#entity-century21-ron` | Discord → bus | Route to entity stream `entity:century21-ron` |

### Access Enforcement (Three Layers)

1. **Discord role assignment** — channel visibility gate enforced by Discord itself.
2. **Bus token scoping** — bot resolves Discord user ID to a known bus identity before publishing to any stream; unauthenticated Discord users cannot inject into bus streams.
3. **Row-level DB security** — any message history fetch through the partner workspace goes through Supabase RLS (`participants` check on `case_threads`).

---

## Test Plan

- [ ] Partner A logs in and CustomerList shows only their assigned cases; direct fetch of another case's API endpoint returns 403.
- [ ] Partner A cannot view `/partner/customers/[id]` for a case assigned to Partner B — middleware rejects at layout level before any data loads.
- [ ] Customer A's `case_messages` are invisible to Customer B's partner — verify with a direct Supabase query using B's JWT.
- [ ] Forensic hash chain integrity: insert 3 messages, corrupt `body` of message 2 in the DB directly, run integrity check script, confirm it flags the break.
- [ ] Kaveh sends a bus message → bridge relays → message appears in Supabase Realtime channel on the partner UI within 2 s.
- [ ] `mint-knight.py --discord-provision` creates the channel, applies overwrites, posts welcome, and writes `discord_channel_id` to Squad Service — all in under 10 s.
- [ ] Customer in `#project-metrobit` cannot fetch or see `#project-cheffs-kitchen` — Discord returns `Missing Permissions`.
- [ ] Partner assigned to Customer A cannot access `#entity-century21-ron` unless they hold that entity role explicitly.
- [ ] Revoking chat consent sets `consent_chat = false` and the compose bar is disabled in the UI; no new rows appear in `case_messages` after revoke.
- [ ] Thread export endpoint returns only messages for the authenticated customer's own thread.

---

## Open Questions

1. **Kaveh Discord identity** — should Kaveh post as the shared `mumega` bot account or get its own Discord application per project? Own application is cleaner but requires more OAuth setup per customer.
2. **SMS mirror consent granularity** — is per-case opt-in sufficient, or does each message type (agent vs. partner) need its own toggle to satisfy PIPEDA's specificity requirement?
3. **Entity channel creation timing** — entity channels (`#entity-century21-ron`) belong to the partner entity, not a specific customer. Should `mint-knight.py --discord-provision` create them if missing, or is that a separate `mint-entity.py` concern?
4. **Hadi admin thread join** — Hadi joining a thread currently requires a direct DB insert. Does this need a UI in an admin panel, or is a CLI one-liner acceptable for now?
5. **Retention vs. PIPEDA right-to-erasure conflict** — CRA mandates 6-year retention for grant/tax records; PIPEDA grants erasure rights. The current spec retains history even after consent revoke. Legal sign-off needed before shipping the consent-revoke flow.
