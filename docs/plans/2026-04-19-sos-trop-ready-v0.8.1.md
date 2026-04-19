# SOS v0.8.1 — TROP-ready

**Date:** 2026-04-19
**Author:** sos-dev (Opus 4.7, planning)
**Target version:** v0.8.1 "TROP-ready"
**Thesis:** Make SOS usable as the coordination spine for a real online-business tenant (TROP). SOS-only work. Everything else is a documented hand-off.

---

## The three-layer rule

SOS is the hub/junction. Every piece below is classified **SOS**, **Mumega**, or **TROP**. Only SOS work is in this plan's scope; Mumega and TROP items are documented hand-offs with contract shapes but no implementation.

- **SOS** — platform-agnostic. Contracts, kernel ports, Python services, clients, agents-as-code. Runs on this server today.
- **Mumega** — the Cloudflare-native product. Workers, D1, KV, DO, public UIs, signup pages.
- **TROP** — the marketplace product. ToRivers API, customer wallet, listing pages, end-user flows.

---

## Current state (checked 2026-04-19)

- `sos/adapters/torivers/bridge.py` — exists, translates Squad tasks. TODOs on completion polling and ToRivers API registration. Workflow catalog of 5 priced automations.
- `sos/adapters/torivers/__main__.py` — CLI runs list/register/execute.
- TROP tickets #97 (pricing) + #98 (`/usage` ingest) closed — metering path works.
- Untracked in-flight SOS files exactly in TROP's shape: `sos/agents/social.py`, `sos/services/content/daily_blog.py`, `sos/services/outreach/`, `sos/services/dashboard/`, `sos/services/operations/organism.py`, `sos/services/operations/pulse.py`.
- v0.8.0 shipped the objectives tree primitive — usable substrate.

## The six sprints

Each sprint ships one shippable piece of SOS. All six bundled = v0.8.1. Executed serially (S3 blocks S4; S5 and S6 can overlap).

---

### S1 — Close the ToRivers bridge loop

**Goal:** Bridge posts objectives instead of squad tasks, waits for completion, returns artifact.

**SOS work (atomic steps):**

- **S1.1** — `sos/adapters/torivers/bridge.py`: replace `_squad_client` calls with `AsyncObjectivesClient`.
    - File: `sos/adapters/torivers/bridge.py`
    - Change: import `AsyncObjectivesClient` from `sos.clients.objectives`; `execute()` calls `client.create(title, bounty_mind=..., tags=["torivers", f"workflow:{name}"], project="trop")`.
    - Outcome: bridge integration test hits a live objectives service (fakeredis) and gets back a real objective ID.

- **S1.2** — `sos/adapters/torivers/bridge.py`: implement completion polling.
    - Change: after `create`, poll `client.get(objective_id)` every 5s up to 5min; return `{status: "completed", artifact_url, task_id}` on state=paid; return `{status: "timeout", task_id}` otherwise.
    - Outcome: test that completes a mock objective mid-poll returns the artifact within 2s.

- **S1.3** — `sos/adapters/torivers/bridge.py`: USD → $MIND conversion.
    - Change: new helper `_usd_to_mind(usd: float) → int` using existing adapter-pricing fixture (trop#97). For v0.8.1, flat 1 USD = 100 $MIND.
    - Outcome: workflow price $25 → bounty 2500 $MIND on the created objective.

- **S1.4** — tests in `tests/adapters/test_torivers_bridge.py`.
    - Change: four tests covering migration (posts objective not squad task), completion (paid → artifact), timeout (not paid in 5min → timeout status), conversion (price → bounty_mind).
    - Outcome: `pytest tests/adapters/test_torivers_bridge.py` green.

**Mumega hand-off:** none.

**TROP hand-off:** TROP's customer-facing UI calls `POST /adapters/torivers/execute` on this server. TROP's wallet debits when bridge returns `status: completed`. TROP owns the refund flow on `status: timeout`.

---

### S2 — TROP as a first-class tenant + standing squad

**Goal:** `project=trop` is a real tenant with its own standing agents. Organism pulse posts their daily objectives.

**SOS work:**

- **S2.1** — Seed trop tenant registration doc.
    - File: `sos/agents/trop/README.md` (new)
    - Change: document how to provision `project=trop` via existing `POST /saas/tenants`. No code — runbook only.
    - Outcome: curl example in doc runs end-to-end against local saas service.

- **S2.2** — Standing agent seeds for TROP.
    - File: `sos/agents/trop/seeds.py` (new)
    - Change: define 4 AgentCards — `trop-social`, `trop-content`, `trop-outreach`, `trop-analytics`. Each with `capabilities`, `project="trop"`.
    - Outcome: running `python -m sos.agents.trop.seeds` posts all 4 cards to `/agents/cards`.

- **S2.3** — Minimum capability matching on objectives listing.
    - File: `sos/services/objectives/__init__.py` (exists)
    - Change: `query_open(*, capability=None, project=None)` already accepts capability filter per v0.8.0 memory. Verify coverage; add test that a `trop-social` card with capability `["post-instagram"]` only sees objectives whose `capabilities_required` matches.
    - Outcome: test in `tests/services/test_objectives_capability_match.py` green.

- **S2.4** — Pulse-to-objective wiring.
    - File: `sos/services/operations/pulse.py` (untracked — finish it)
    - Change: daily pulse posts a root objective `<project>-daily-rhythm` via `AsyncObjectivesClient`, with children per standing workflow. Read standing-workflow list from `sos/agents/trop/workflows.py`.
    - Outcome: `python -m sos.services.operations.pulse --project trop` posts one tree with N children.

**Mumega hand-off:** Signup page at app.mumega.com takes a user-friendly form → POSTs `/saas/tenants`. Owner dashboard shows `project=trop` objectives live. CF-native.

**TROP hand-off:** TROP funds $MIND via Stripe → existing billing webhook → existing economy.credit. No new path.

---

### S3 — Outcome-scored ack (contract change)

**Goal:** Ack carries a numeric score, not just true/false. Unblocks the auto-improvement loop.

**SOS work:**

- **S3.1** — Contract: add optional `outcome_score: float | None = None` to `Objective`.
    - File: `sos/contracts/objective.py`
    - Change: add field + docstring. Update `to_redis_hash` / `from_redis_hash` JSON-encoding for the float.
    - Outcome: existing 17 contract tests green; add 2 new (score round-trips, defaults to None).

- **S3.2** — Schema snapshot regeneration.
    - File: `sos/contracts/schemas/objective.schema.json`
    - Change: regenerate; update drift test fixture.
    - Outcome: `pytest tests/contracts/test_objective_schema.py` green.

- **S3.3** — Ack route accepts `outcome_score` in body.
    - File: `sos/services/objectives/app.py`
    - Change: `POST /objectives/{id}/ack` body model adds `outcome_score: float | None = None`. Stored on the objective when provided. Does NOT change the completion gate — payout stays binary for v0.8.1.
    - Outcome: 2 new app tests (ack with score stores; ack without score still works).

- **S3.4** — Audit event carries score.
    - File: `sos/services/objectives/app.py` → `_emit_audit`
    - Change: include `outcome_score` in the audit XADD payload.
    - Outcome: test asserts the Redis stream entry contains the score.

**Mumega hand-off:** none.

**TROP hand-off:** TROP's analytics agent reads back engagement (CTR, conversions, revenue attribution), POSTs `/ack` with `outcome_score ∈ [0.0, 1.0]` or raw value per agreed convention. TROP owns the metric definition per workflow.

---

### S4 — Curator + demo bank (RAG, not fine-tune)

**Goal:** Winners become retrieval examples. Compounding loop starts.

**SOS work:**

- **S4.1** — Kernel helper: `fetch_winners(role, n=10, project=None)`.
    - File: `sos/kernel/demo_bank.py` (new)
    - Change: queries the memory service for memories tagged `role:<role>` and `kind:winner`, ordered by outcome_score desc. Returns list of dicts with `{prompt, artifact, score}`.
    - Outcome: unit tests against mocked memory service.

- **S4.2** — Curator agent.
    - File: `sos/agents/curator.py` (new)
    - Change: standing agent, claims objectives tagged `kind:harvest-winners`. On claim: reads `sos:stream:global:objectives` audit tail; filters to paid objectives with `outcome_score ≥ threshold`; writes each as a memory entry.
    - Outcome: integration test that seeds 20 audit events, curator ships 2 top-decile entries into memory.

- **S4.3** — Daily harvest objective in pulse.
    - File: `sos/services/operations/pulse.py` (touched in S2.4)
    - Change: nightly pulse posts `harvest-winners` per active project.
    - Outcome: runs against fakeredis end-to-end.

- **S4.4** — Opt-in hook for agents.
    - File: `sos/kernel/demo_bank.py`
    - Change: add convenience `build_few_shot_prompt(role, base_prompt, n=10)` that appends winner examples. Agents choose to call it.
    - Outcome: docstring example + test.

**Mumega hand-off:** none.

**TROP hand-off:** TROP's own agents opt-in by calling `fetch_winners("social", project="trop")` inside their prompt logic. TROP chooses whether a workflow runs with or without the demo bank.

---

### S5 — Operator dashboard API (SOS) + UI hand-off (Mumega)

**Goal:** SOS exposes the per-tenant view via API. The pretty UI belongs to Mumega.

**SOS work:**

- **S5.1** — Finish `sos/services/dashboard/` scaffold.
    - Files: `sos/services/dashboard/__init__.py`, `app.py`, `__main__.py` (all untracked — finish)
    - Change: FastAPI app on port 6069. Auth via existing gate. Routes:
        - `GET /dashboard/tenants/{project}/summary` — counts of open/claimed/shipped/paid in last 24h + $MIND burn.
        - `GET /dashboard/tenants/{project}/agents` — live AgentCards for this project.
        - `POST /dashboard/agents/{name}/kill` — admin-only; writes `agent:{name}:killed=1` to Redis.
    - Outcome: curl + pytest integration coverage.

- **S5.2** — Kill-switch enforcement.
    - File: `sos/kernel/auth.py` or a new `sos/kernel/kill_switch.py`
    - Change: central check `is_agent_killed(name) → bool`; auth middleware rejects requests from killed agents (401).
    - Outcome: test that a killed agent's token returns 401 even if otherwise valid.

- **S5.3** — Import-linter contract update.
    - File: `pyproject.toml`
    - Change: add `sos.services.dashboard` to R1 modules list.
    - Outcome: `lint-imports` green with dashboard listed.

**Mumega hand-off (documented shape, not built):**
- Customer UI at `app.mumega.com/dashboard/{project}` — CF Pages + Workers.
- Session auth against Mumega's user table; mints a SOS bearer via existing saas service.
- Live updates via EventSource on `/dashboard/stream` (SSE — SOS exposes; CF proxies).
- Per-user preferences (collapsed panes, favorite views) stored in CF D1 (Mumega's concern, not SOS's).
- Kill button calls `POST /dashboard/agents/{name}/kill` via the CF Worker.

**TROP hand-off:** TROP's customer-facing "my automations" UI at trop.domain — entirely separate product. Polls same `GET /dashboard/tenants/trop/summary` for its stats widget.

---

### S6 — Organism rhythm (finish operations/organism + pulse)

**Goal:** The daily heartbeat that makes the whole tree run itself.

**SOS work:**

- **S6.1** — Finish `sos/services/operations/organism.py` (untracked).
    - Change: cron-like loop that runs once per minute; invokes pulse per active project; handles degraded mode (if objectives service down, log-only, don't raise).
    - Outcome: systemd unit `sos-organism.service` documented; process stays up for 10 minutes under fakeredis smoke test.

- **S6.2** — Finish `sos/services/operations/pulse.py` (touched in S2.4, S4.3).
    - Change: three scheduled pulses per project — morning (daily rhythm), noon (health check), evening (harvest + postmortem).
    - Outcome: each pulse posts the expected objectives.

- **S6.3** — Postmortem objective per paid root.
    - Change: on every root objective paid, pulse posts `postmortem-<root_id>` — curator claims, writes a summary memory, acks.
    - Outcome: integration test.

**Mumega hand-off:** none.

**TROP hand-off:** TROP subscribes to the organism pulse via bus (`sos:stream:global:objectives`) for its own analytics dashboard. Read-only consumer.

---

## Sprint order + dependencies

```
S1 ──┐
     ├── independent of S2
S2 ──┘
         ├── S3 (no dep) ──── S4 (requires S3's outcome_score)
         └── S5 (no dep) ──── S6 (requires S2.4 pulse file, S4.3 harvest)

Bundle: S1 + S2 ship together → alpha. S3 + S4 → beta. S5 + S6 → rc → v0.8.1 tag.
```

## Squad (picked once, reused across all six)

| Role | Agent | Used for |
|---|---|---|
| Fast search | Explore (haiku) | Locate untracked files, trace imports |
| Parallel executor | general-purpose (sonnet) × 2 | S1 / S2 / S3 / S5 in parallel where non-conflicting |
| Stateful specialist | Kasra (opus) | S4 (curator spans kernel+agent+test), S6 (organism+pulse+postmortem spans multiple files) |
| Architectural gate | Athena (opus) | Review before each milestone bundle |
| Safety net | sos-medic | On-call if tests go red unexpectedly |
| Final gate | superpowers:code-reviewer | Once at v0.8.1 rc before version bump |

## Acceptance gates for v0.8.1

- All six sprints' tests green
- Full suite `pytest tests/ sos/tests/` green (modulo pre-existing flakes)
- `lint-imports`: 4 contracts KEPT (or 5 if dashboard added), 0 broken
- `version = "0.8.1"` in pyproject.toml
- CHANGELOG: new `## [0.8.1] — TROP-ready` section at top
- Bridge integration smoke: `python -m sos.adapters.torivers --execute monthly-seo-audit --input '{"domain":"trop.example"}'` returns `status: completed` or `status: timeout` (not `status: dispatched`)

## Explicit non-goals for v0.8.1

- v0.8.2 decay sweeper (still its own sprint)
- Subscription-by-subtree push (v0.8.1+)
- Fine-tune orchestrator (v0.8.3+; curator is RAG only)
- Mumega dashboard UI (Mumega repo, not here)
- ToRivers customer-facing site (TROP repo, not here)
- Bedrock provider adapter (explicitly dropped per 2026-04-19 user direction)
- New vertical adapters beyond torivers (deferred)

## Hand-off contracts (for Mumega + TROP teams)

All contracts are HTTP over the dispatcher. Tokens via existing bearer scheme.

**Mumega owes:**
- CF Pages at `app.mumega.com/dashboard/{project}` consuming `GET /dashboard/tenants/{project}/summary`
- Signup page POSTing to `/saas/tenants`
- SSE proxy for `/dashboard/stream`

**TROP owes:**
- Customer site calling `POST /adapters/torivers/execute`
- Wallet debit on bridge `status: completed` return
- Analytics agent posting `POST /objectives/{id}/ack` with `outcome_score`

**SOS owes (delivered by v0.8.1):**
- The six sprints above
- Stable contract shape — no further breaking changes to `Objective` or `/adapters/torivers/*` without a v0.9 bump
