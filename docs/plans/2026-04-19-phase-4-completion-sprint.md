# Phase 4 — Completion Sprint (v0.9.3)

**Date:** 2026-04-19
**Task:** #209
**Blocks:** #205 (EPIC Mumega Mothership), downstream Phase 5-ongoing work
**Builds on:** `docs/plans/2026-04-19-mumega-edge-canonical-api.md` (route design, scope, risks)

## Encapsulation rule (revised 2026-04-19)

**SOS does not reach into sibling repos.** Findings that concern Inkwell
(`Mumega-com/inkwell`), per-instance forks (`Mumega-com/*-inkwell`), or
`mumega-edge` are filed as GitHub issues against the owning repo, and
that repo's own agent executes the work. This replaces the earlier
"dispatch a no-context Sonnet to bash-edit sibling files" pattern.

Saved as feedback memory `feedback_repo_encapsulation.md`.

In this sprint:
- Waves that touch the SOS repo (W3 release mechanics) → dispatch locally.
- Waves that touch any other repo → file a GH issue and stop.
- SOS Medic still runs cross-repo health probes (those are reads, not writes).

## Scope

Only the three remaining Phase 4 steps. Steps 4.1–4.3 and 4.6 already shipped in `mumega-edge` (tasks #259, #260, #261, #262).

| Step | Owner repo | Status |
|---|---|---|
| 4.4 Inkwell config default | `/home/mumega/inkwell/` | pending |
| 4.5 Per-instance Worker removal | `digid-inkwell`, `shabrang-inkwell`, `mumega-internal-inkwell` | pending, destructive |
| 4.7 Ship v0.9.3 | this repo (SOS) + release mechanics | pending |

Anything else is out of scope. If a wave subagent surfaces a new-shaped problem, it stops and reports — no widening.

## Version note

`pyproject.toml` is already at `0.10.1` (Phase 7 shipped). `v0.9.3` is a retro-tag at the commit that closes Phase 4 on SOS side. Tag-from-commit, don't rewrite version numbers. Tags currently present: `v0.9.0 → v0.9.1 → v0.9.2 → v0.9.2.1 → v0.9.4`. The gap at `v0.9.3` is what this sprint fills.

## The squad — one composition across the sprint

No rotation, no new specialists.

| Role | Model | Waves |
|---|---|---|
| Integrator | Opus (this session) | All waves — dispatch, conflict resolution, cross-repo coordination, commit authorship |
| SOS Medic | Stateful agent | Standing watch across all waves (probes only, no writes into sibling repos) |
| Haiku | Haiku (no-context) | W3 — SOS-side release mechanics (only SOS-repo wave left) |

Sonnet A/B slots were in the earlier draft. After the encapsulation rule
landed, W1 and W2 became GH-issue filings (not dispatches), so no Sonnet
is needed in this sprint. W0 already executed under the old pattern and
is frozen for historical reference below.

### No-context subagent brief contract

Each Sonnet/Haiku brief is self-contained. Minimum required fields:

1. **Goal** — one sentence, what "done" looks like.
2. **Target paths** — absolute paths of every file touched. No globs, no "find the file".
3. **Diff intent** — the exact shape of the change (old → new).
4. **Acceptance** — command to run + expected result (e.g. `grep -n workerUrl inkwell.config.ts` → expected line).
5. **Guardrails** — do-not-widen rule; if a file outside target paths needs editing, stop and report.
6. **Commit format** — `<verb>(phase-4/Wn): <what>`; main-branch discipline; HEREDOC body.
7. **Out-of-scope flags** — repos not to touch this wave, known adjacent work already done.

Subagents do not talk to each other. Opus is the bus. Every handoff returns to this session.

### SOS Medic duties

The only stateful agent in this squad. Across all waves:

- Tail `api.mumega.com` smoke probes (`/health`, `/sos/registry/mesh`, `/inkwell/glass/tiles`) — record baseline latencies before W4, watch for regression after.
- Confirm the `scripts/sync-tokens-to-kv.py` cron (task #38, shipped) is still executing — this is the stated risk in the existing Phase 4 plan.
- After each per-instance cutover (W2 sub-brief), probe the Pages site's `/dashboard` for 200 + session-cookie handshake. Record a signature per fork so later cutovers reuse the same health check.
- Diagnoses only. Fixes belong to Opus + the wave Sonnet.

## Waves

Each wave = one revertable commit or coordinated deploy. Same discipline as Phase 2 and Phase 3.

### W0 — Inkwell config default ✅ shipped 2026-04-19 (pre-encapsulation-rule)

- **Repo:** `Mumega-com/inkwell` (`/home/mumega/inkwell/`)
- **Executed as:** Sonnet A no-context subagent (the earlier pattern; would be a GH issue under the revised rule).
- **Commit:** `4ef129e` on `main` — `workerUrl: 'https://api.mumega.com'` at `inkwell.config.ts:160`.
- **Follow-up finding:** Inkwell template carries its own `workers/inkwell-api/`. Filed as `Mumega-com/inkwell#30` — Inkwell agent resolves.
- **Leave as-is.** One-line mechanical edit, green, correct. Reverting for pattern-purity would be churn.

### W1 — Per-instance fork audit (GH issue, Inkwell agent executes)

Under the encapsulation rule, SOS does not crawl sibling repos to build
an audit report. The audit itself is the Inkwell agent's work.

- **Action:** `gh issue create --repo Mumega-com/inkwell` asking the Inkwell
  agent to produce the fork status report (workerUrl value, presence of
  `workers/inkwell-api/`, Pages project name, last deploy SHA) for each
  known per-instance fork: `digid-inkwell`, `shabrang-inkwell`,
  `mumega-internal-inkwell`, and any others the Inkwell agent is aware of.
- **Companion issue:** separately file on each fork repo (`Mumega-com/digid-inkwell`,
  etc.) a "Phase 4 readiness" tracking issue — so each fork's own agent
  (if one exists) has a ticket on its own surface.
- **Acceptance:** issues are open and referenced in this plan. Report
  comes back as issue comments, not as an SOS-side deliverable.
- **No SOS commit.** Cross-repo coordination only.

### W2 — Per-instance cutover (GH issues, fork agents execute, Hadi gates deploys)

Each fork owns its own cutover. SOS files the issue, the fork's agent
does the work, Hadi gates the live Pages redeploy.

- **One GH issue per fork repo**, via `gh issue create --repo Mumega-com/<fork>-inkwell`:
  - Title: `Phase 4 cutover — retire workers/inkwell-api, inherit api.mumega.com default`
  - Body: mothership link, exact diff intent (delete `workers/inkwell-api/`,
    remove local `workerUrl` override from `inkwell.config.ts`),
    acceptance (dashboard probe 200 + session cookie post-redeploy),
    ordering note (`mumega-internal` first).
  - Labels: none until label taxonomy exists in each repo.
- **Ordering (suggested, the fork agent decides final):**
  1. `mumega-internal-inkwell` — dogfood
  2. `shabrang-inkwell`
  3. `digid-inkwell`
  4. Any additional forks surfaced by W1 audit
- **SOS Medic's role:** after each Pages redeploy lands, run the dashboard
  probe + `api.mumega.com/sos/registry/agents` check and comment the
  result on the fork's issue. Probes are reads into DNS + HTTP — not
  writes into the fork repo — so encapsulation holds.
- **Non-reversible deploys stay Hadi-gated** per fork.

### W3 — SOS-side release mechanics (Haiku)

- **Repo:** this one.
- **Files:**
  - `/mnt/HC_Volume_104325311/SOS/CHANGELOG.md` — add `[0.9.3] — 2026-04-19 — Phase 4 Mumega-edge canonical API` entry above `[0.10.0]`. Summarize: single-ingress `api.mumega.com`, SOS-proxy + Inkwell route groups, per-instance Workers retired, one Hono router. Reference tasks #259, #260, #261, #262, #209.
  - No `pyproject.toml` change — version is already past.
- **Acceptance:** `head -60 CHANGELOG.md` shows the new section; markdown lint clean.
- **Commit:** `docs(changelog): v0.9.3 — Phase 4 Mumega-edge canonical API` in SOS repo.
- **Tag (Opus, post-commit):** `git tag -a v0.9.3 -m "Phase 4 — Mumega-edge canonical API" <commit>` at the W3 commit or at the last mumega-edge-touching commit in SOS — pick whichever makes the tag chronology monotone.

### W4 — Live deploy (Opus-dispatched, Hadi-executed)

- **Action:** `wrangler deploy` on `mumega-edge`. Cuts `api.mumega.com` over to the unified router.
- **Pre-flight (SOS Medic checks):**
  - `TOKENS` KV in `mumega-edge` has the current entries from `sos/bus/tokens.json`.
  - `api.mumega.com` DNS + CF route confirmed in dashboard.
  - CORS allow-list in `mumega-edge` env covers `digid.com`, `shabrang.ai`, `mumega.com`, `*.pages.dev` during transition.
- **Smoke (SOS Medic, 10 min post-deploy):** probes on `/health`, `/sos/registry/mesh`, `/inkwell/glass/tiles`, one auth flow (`/auth/me` with a live session cookie).
- **Rollback:** `wrangler rollback` to prior deployment SHA if any smoke fails.
- **Non-reversible within SLA.** Hadi approval + Hadi-runs-the-command.

## Dispatch order (post-encapsulation)

```
Opus (integrator)
  ├── W0 ✅ shipped 2026-04-19 as Mumega-com/inkwell@4ef129e
  │        ↳ follow-up: Mumega-com/inkwell#30 (template workers/inkwell-api role)
  ├── W1 → gh issue create --repo Mumega-com/inkwell (audit request)
  │        ↳ plus one Phase-4-readiness issue per fork repo
  │        ↳ Inkwell agent returns report as issue comments
  ├── W2 → (Hadi approval gate — per fork)
  │        ↳ one GH issue per fork, fork agent executes cutover
  │        ↳ Medic probes each fork post-redeploy, comments on issue
  ├── W3 → Haiku brief (SOS CHANGELOG — SOS repo, local dispatch OK)
  │        ↳ Opus tags v0.9.3
  └── W4 → (Hadi approval gate)
           ↳ gh issue create --repo Mumega-com/mumega-edge (deploy readiness)
           ↳ Hadi runs `wrangler deploy` when mumega-edge agent confirms ready
           ↳ Medic runs 10-min smoke, comments on issue
           ↳ sprint closes, #209 → completed, #205 unblocks next phase
```

## Exit gate

Task #209 marked completed when all of:

1. `api.mumega.com` returns 200 on `/health`, `/sos/registry/mesh`, and `/inkwell/glass/tiles` under the unified router.
2. All three named per-instance forks have zero `workers/inkwell-api/` subtree and their Pages sites pass the dashboard probe.
3. Tag `v0.9.3` exists in SOS repo with CHANGELOG entry.
4. SOS Medic's 10-min post-deploy smoke is clean (no CORS regressions, no auth handshake drops).

## Out-of-scope

- Any `mumega-edge` route changes — that work closed in tasks #260, #261.
- Phase 5 `sos init` helpers — they run on top of this but don't block it.
- Retiring the legacy VPS:8075 direct-hit routes — per existing plan's risk note, keep live 2 weeks post-cutover; retirement goes in v0.9.4 scope (Phase 5 already shipped under a different tag, so this becomes a v0.9.5 cleanup task).
