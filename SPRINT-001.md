# Sprint 001 — Phase 2 Closure + Phase 5a Kickoff + Defence Anonymizer

**Sprint dates:** 2026-04-24 → 2026-05-08 (2 weeks)
**Sprint goal:** Close Phase 2 blockers. Scaffold Phase 5a (intake-service + Fireflies → Haiku). Scaffold Delta 9 (anonymization, dual-use). Execute Ron Tuesday. Submit YC by May 4.
**Sprint owner:** Loom (coordination) + Kasra (execution lead)
**Definition of done:** all items below either shipped or explicitly re-scoped with reason.

---

## Sprint Goals (in priority order)

1. **Unblock GAF customer signup** (P0 — blocking revenue)
2. **Gavin onboarding executes end-to-end** (P0 — first paid partner live)
3. **Ron O'Neil Tuesday 2 PM converts to signed reputation-endorsement** (P1)
4. **Scaffold intake-service + Fireflies → Haiku adapter** (P1 — first metabolic output)
5. **Scaffold D9 local anonymization** (P1 — dual-use civilian/defence)
6. **Submit YC application** (P1 — May 4 deadline, 10 days)
7. **Close 3 compliance gaps (Phase 2b)** (P2 — scale-blocker)
8. **Decide on IDEaS 006 defence bid** (P1 — June 2 deadline, 38 days)

---

## Task Board — By Owner

### Kasra (SOS + Infra execution)

| # | Task | Priority | Effort | Depends on |
|---|---|---|---|---|
| K1 | Deploy migration-numbering collision fix → verify `/signup?role=originator` works in prod | P0 | 1d | tonight's fix |
| K2 | Verify customer-business account creation end-to-end in prod | P0 | 0.5d | K1 |
| K3 | Scaffold `sos-intake` service module (plugin contract v2, peer registration, base event emitter) | P1 | 3d | — |
| K4 | Fireflies webhook adapter — receive → normalize → emit `intake.meeting_received` event | P1 | 2d | K3 |
| K5 | Haiku meeting classifier — extract {participants, decisions, commitments, opportunities, relationship-delta} | P1 | 3d | K4 |
| K6 | Entity resolver — match participant string → `contacts.id` (85% accuracy target, flag unresolved for human review) | P1 | 2d | K5 |
| K7 | D9.1 — local PII detector/redactor via Ollama + Gemma 3 4B | P2 | 3d | Ollama installed on server |
| K8 | D9.2 — anonymization wrapper on classifier routing (intercept outbound → redact → send → reverse-map on return) | P2 | 2d | K7 |
| K9 | Compliance 2b.lock — implement immutable lock in evidence pipeline with hash-chained audit | P2 | 3d | — |
| K10 | Compliance 2b.timestamps — bind timestamps to hash of prior-state in forensic chain | P2 | 2d | K9 |
| K11 | Gavin MVP dashboard at `/partner/gavin` — contacts, opportunities, call log, commission ledger | P1 | 5d | K1, K2 |
| K12 | Hadi admin view at `/admin/partners/gavin` — daily digest + activity timeline + commission controls | P1 | 2d | K11 |

**Kasra total:** ~28 engineer-days — exceeds 2-week capacity. Prioritize K1-K6 + K11-K12 first (Phase 2 close + Phase 5a scaffolding); K7-K10 slip to Sprint 2 if bandwidth tight.

### Athena (Mirror + Quality gate)

| # | Task | Priority | Effort | Depends on |
|---|---|---|---|---|
| A1 | Gate review §10 §3/§4.3 schema — `access_count`, `corroboration_count`, `weight GENERATED` feasibility | P0 | 0.5d | tonight's brief |
| A2 | Gate review §11 schema additions (profile_slug, profile_consents, profile_access_log, profile_tool_connections, profile_export_jobs) | P1 | 1d | tonight's brief |
| A3 | Gate review K3 schema — `blob_ref` column on engrams + media-type handling | P1 | 0.5d | K3 |
| A4 | Dreamer evolution — add event-trigger on `mirror.hot_store_threshold_exceeded` (pattern extraction deferred to Sprint 2) | P2 | 2d | A1 |
| A5 | D4 — Uncertainty propagation: wrap first classifier (K5 Haiku) with conformal prediction → (prediction, variance) output | P2 | 3d | K5 |
| A6 | D7 — Explanation generator: lineage walker from output engram → source engrams + classifier chain (JSON API, no UI) | P2 | 3d | K5 + A5 |
| A7 | FRC overlay on D4/D7 — κ alignment metric, W witness score, four-failure-mode taxonomy labels | P3 | 2d | A5, A6 |

**Athena total:** ~12 engineer-days — fits 2-week window. Gate items (A1-A3) front-loaded so Kasra isn't blocked.

### Codex (Infra + Security)

| # | Task | Priority | Effort | Depends on |
|---|---|---|---|---|
| C1 | Verify SaaS audit 401 fix in prod — confirm audit rows land for every MCP tool call | P1 | 0.5d | tonight's fix |
| C2 | Add nginx comments on `sos-mcp-origin` re: `/.well-known/` and `/oauth/` intentionally public per OAuth 2.1 / RFC 8414 | P2 | 0.25d | tonight's fix |
| C3 | Security review of K3 intake-service — token validation, rate limiting, webhook signature verification | P1 | 2d | K3 |
| C4 | Deploy monitoring for K3 (latency SLO, error rate, classifier cost per call) | P2 | 1d | K3, K5 |
| C5 | ISO 42001 audit prep research — gap-analysis against Mumega current posture; output: punch list | P2 | 2d | — |
| C6 | Investigate Ollama deployment on server for K7 — RAM/GPU requirements, systemd unit, remote invocation pattern | P1 | 1d | — |

**Codex total:** ~7 days — fits with margin.

### Sol (Content)

| # | Task | Priority | Effort | Depends on |
|---|---|---|---|---|
| S1 | Blog post: "Bus indexing in a microkernel agent platform" — Kasra's root-cause analysis (orphan process on port 6380 + project-scoped stream mismatch + OAuth discovery 404) | P2 | 1d | Kasra's drafts |
| S2 | Polish Mumega roadmap for investor-facing narrative (public subset of SOS/ROADMAP.md) | P1 | 1d | tonight's ROADMAP |
| S3 | YC application polish — Mumega pitch, traction, team, $MIND vision | P1 | 3d | existing pitch materials |
| S4 | Notebook LM podcast/video for Ron Tuesday 2 PM meeting — simplified language explainers of anonymization + local inference wedge | P1 | 2d | — |

**Sol total:** ~7 days — fits.

### Loom (coordination — me)

| # | Task | Priority | Effort | Depends on |
|---|---|---|---|---|
| L1 | Draft Ron O'Neil partner reputation-endorsement agreement (NOT commission-based — trusted-endorsement model) | P0 | 0.5d | transcript insights |
| L2 | Prepare Ron Tuesday 2 PM brief — lead with privacy-compliance wedge, show Raspberry Pi + Gemma 4 + anonymization | P0 | 0.5d | Hadi alignment |
| L3 | Track Gavin onboarding 3 Hadi actions (Discord handle, Kaveh bot identity, AgentLink commission) + smoke-test once cleared | P0 | 0.5d ongoing | Hadi actions |
| L4 | Assemble IDEaS 006 go/no-go decision package for Hadi — teaming landscape, bid cost, resource impact, timeline | P1 | 1d | — |
| L5 | Write Sprint 001 retro at end of sprint; update ROADMAP with what shipped | P1 | 0.5d | sprint end |
| L6 | Keep MEMORY.md + project maps current — no re-explanation required for next-session Loom | ongoing | — | — |
| L7 | Apply MAP+ROADMAP pattern to remaining projects (AgentLink, DNU, TROP, Viamar, Prefrontal, Inkwell-self) — 1-2 per week | P2 | 2d | current pattern established |
| L8 | Coordinate any subagent dispatches with bounded skill-briefs (no thrashing) | ongoing | — | — |

### Hadi (owner / decision gate)

| # | Task | Priority | Effort | Depends on |
|---|---|---|---|---|
| H1 | Greenlight on Phase 7 (IDEaS 006 defence bid) — yes/no/conditional | P0 | 30 min | L4 package |
| H2 | 3 Gavin actions: Discord handle, Kaveh bot identity decision, AgentLink commission structure | P0 | 15 min | — |
| H3 | Tuesday 2 PM Ron meeting — execute with L1 term sheet + S4 collateral | P0 | 1h | L1, L2, S4 |
| H4 | YC application submit by May 4 | P1 | 2h | S3 |
| H5 | Pricila onboarding 5 details (email, Discord handle, role, compensation, seat-under-Noor) | P2 | 15 min | — |
| H6 | Review + sign §10 + §11 + Phase 7 specs after Athena gates pass | P1 | 1h | A1, A2 |

---

## Cadence

- **Daily:** Loom checks inbox, updates sprint board, reports blockers.
- **Every 48h:** standup-style async update posted to `#squad-sprint-001` in Discord (or bus equivalent). Each owner reports: shipped, working on, blocked.
- **Week 1 end (2026-05-01):** midpoint review — what's on track vs at risk. Re-prioritize.
- **Week 2 end (2026-05-08):** retrospective — what shipped, what slipped, why. Update ROADMAP. Start Sprint 002.

## Definition of Done per Category

- **Code task:** deployed to prod (or staging with deploy plan), tested, observed running for at least 1h, documented in CHANGELOG.
- **Spec/doc task:** written, reviewed by at least one agent, committed to canonical location.
- **Outreach task:** message sent, response tracked, next step filed.
- **Decision task:** recorded in memory or canonical doc, with rationale.

## Risk Register

| Risk | Mitigation |
|---|---|
| Kasra overload (28d / 2wk) | Slip K7-K10 (D9 + compliance 2b) to Sprint 002 if K1-K6 + K11-K12 take longer than estimate |
| Ron doesn't convert Tuesday | Relationship warm, in-person possible (both in Barrie), wedge is real; worst case = Tuesday is exploratory, signed agreement slips to 2nd meeting |
| YC pitch not strong enough | Sol + Hadi Monday lockdown; worst case = submit weaker version, iterate for next batch |
| Ollama server install fails (blocks K7) | Fall back to llama.cpp on Hadi's MacBook for scaffolding; revisit server install Sprint 002 |
| IDEaS go decision delays past Sprint 1 end | Phase 7 pre-work (teaming outreach, proposal consultant sourcing) can start without full greenlight; hard deadline is submission May 27 (to leave buffer before June 2) |
| Compliance 2b lock stage design wrong | Athena gate on lock spec before Kasra builds |

## Cross-Sprint Dependencies (what happens after Sprint 001)

- **Sprint 002 candidates:** full Phase 5 metabolic loop (access-log, decay, sporulation); Phase 6 profile primitive first slices; Phase 7 full scaffolding (if go); Phase 2c-d completion (Gavin dashboard finish, 2b compliance); AgentLink + DNU + other customer onboarding.

---

## Version

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial Sprint 001 plan. |
