# Changelog

All notable changes to SOS (Sovereign Operating System) will be documented here.

## [Unreleased]

---

## [0.9.1] — 2026-04-19 — Bus stability: project-scope + ack-or-retry

### Thesis
Phase 2 of the Mumega Mothership plan. Two silent-failure modes on the bus
closed: scope leaks (messages without `tenant_id`/`project` could cross
tenants) and silent drops (fire-and-forget publish had no retry / DLQ on
the consume side). Each wave shipped as one revertable commit; consumers
migrated incrementally without breaking the backwards-compat global stream.

### Added — Contract + delivery primitives
- **W0** — `BusMessage.tenant_id` + `BusMessage.project` are now required.
  JSON schema regenerated; `tests/contracts/test_bus_port.py` locks it
  with a snapshot test. `sos/contracts/ports/bus.py`.
- **W1** — `kernel.bus.send()` requires `tenant_id` + `project` arguments
  and raises on missing scope; `sos/services/bus/enforcement.py` validates
  the scope at publish. `sos_mcp_sse.py` threads scope from the caller's
  bearer-token context. No message leaves publish without a scope.
- **W2** — `BusPort.ack(message_id, status)` primitive with `BusAck` model
  (`{message_id, acked_at, status: Literal["ok","nack","dlq"]}`).
  `sos/services/bus/redis_bus.py` implements it against Redis Streams XACK.
  Integration tests in `tests/integration/test_bus_ack.py`.
- **W3** — `sos/services/bus/retry.py` — background retry worker scans
  `XPENDING` every 30s; exponential backoff 30s → 2m → 10m → DLQ.
  `register_consumer_group` lazily boots the worker per service.
- **W4** — `sos/services/bus/dlq.py` — shared DLQ schema + read helpers
  (`DLQEntry`, `dlq_stream_for`, `read_dlq`, `list_dlq_streams`) so writer
  (retry worker) and readers (dashboard) can't drift on field names.
  Dashboard route `GET /sos/bus/dlq?stream=` surfaces recent DLQ entries.

### Changed — Consumers migrated to at-least-once
- **W5** — `sos/services/journeys/bus_consumer.py` pilot migration from
  XREAD + Redis checkpoints + in-memory LRU to XREADGROUP + XACK consumer
  group. Handler exceptions now leave entries unacked so the W3 retry
  worker can reclaim them; no more silent drops. Envelope-level `_LRUSet`
  retained because retry re-XADDs produce new stream entries with the
  same `message_id`.
- **W6** — `sos/services/brain/service.py` and
  `sos/services/health/bus_consumer.py` migrated to the same XREADGROUP +
  XACK pattern. Brain preserves its `BrainState`, snapshot persistence,
  OTEL trace_id context, and task.scored / task.routed emission.
  (Scope shrank from the plan's original 4 consumers: recon confirmed
  `feedback/`, `operations/`, and `saas/` do not run bus consumers;
  `execution/worker.py` uses a task-queue pattern that doesn't belong to
  this migration.)

### Tests
- `tests/contracts/test_bus_port.py` — scope-required contract + schema snapshot.
- `tests/integration/test_bus_ack.py` — ack primitive (process → ack;
  no-ack leaves XPENDING; nack routes to retry).
- `tests/integration/test_bus_retry.py` — unacked re-delivered after
  backoff; 3 retries then DLQ.
- `tests/integration/test_bus_dlq.py` — retry writer produces
  parseable `DLQEntry`; `list_dlq_streams` surfaces originals.
- `tests/integration/test_journeys_consumer.py`,
  `tests/integration/test_health_consumer.py`,
  `tests/integration/test_brain_consumer.py` — crashed-mid-handler →
  unacked → retry redelivers → restarted consumer processes. Covers the
  exact regression the migration protects against.
- 13 bus integration tests pass against real Redis; per-service unit
  suites green.

### Rollback posture
Each wave is independently revertable. W5/W6 consume the W2 primitive, not
W3 directly — reverting W3 makes retry disappear but doesn't break the
consumers. W0 is the hard one (contract break); rolling it back would
require v0.9.1.1 making `project` optional again.

### Non-goals for v0.9.1
- Not moving off Redis (CF Queues = v0.9.3+ with Mumega-edge).
- Not fan-out / partitioning.
- Not changing outbound publish to sync; ack is consume-side only.

### Also in v0.9.1 — Phase 1.5 (stability interstitial, landed before Phase 2)
- `.pre-commit-config.yaml` — fast stage (ruff, black, import-linter,
  port schema drift) on every commit; thorough stage (`pytest
  tests/contracts/`) on every push. Ruff and black run on CHANGED files
  only, so pre-existing drift is grandfathered — new commits must pass
  on their own changes. `import-linter` and `contracts-check` run
  repo-wide because they already pass on the current tree.
- `scripts/install-hooks.sh` — one-command bootstrap for pre-commit +
  pre-push hooks. Prefers `.venv/bin/pre-commit`; falls back with a
  clear error if no venv exists.
- Reformatted the 15 port modules under `sos/contracts/ports/` so the
  v0.9.0 artifact is black-clean on Py3.10+.
- `.github/workflows/ci-deploy.yml` — CI now runs the same hook scripts
  via `pre-commit run` instead of bespoke lint+test steps. Structural
  checks (import-linter + port schemas + contract tests) run
  repo-wide; format checks are trusted to the local hook on touched
  files (matches the grandfathering policy).

### Removed
- `.github/workflows/ci.yml` — never-committed duplicate of
  `ci-deploy.yml` with broken lint gates. Merged into `ci-deploy.yml`.

### Intent
Every commit — Codex, Kasra, Claude, manual, any future agent — passes
the same invariants before it can land. New drift blocked; existing
drift addressed as files are touched.

---

## [0.9.0] — 2026-04-19 — Shared port registry + microkernel hardening

### Thesis
Phase 1 of the Mumega Mothership plan: the 14 canonical SOS ports now
have a single source of truth — Pydantic contracts in
`sos.contracts.ports.*` — and both Python (SOS) and TypeScript
(Inkwell) consume them from the same schemas. This closes the drift
surface between sovereign runtime and product surface, and sets the
microkernel boundary: product-specific code (Kay Hermes, ToRivers
tenant, Mumega.com, agency lead-gen) lives outside `sos/`.

### Added — port registry (Phase 1)
- `sos/contracts/ports/` — 14 canonical ports: agent, auth, bus,
  content, content_source, crm, database, economy, graph, media,
  memory, search, session, storage. Each port is a Pydantic v2 module
  exposing request/response models + a `Protocol` describing the
  async method surface.
- `sos/contracts/ports/media.py` — 14th port (12 models + 9-method
  MediaPort Protocol): upload, get, describe, transcribe, transform,
  search, list, delete, generate_image. Tenant binding semantics
  documented; `generate_image` uses snake_case on the Python side and
  is rendered as `generateImage` in TS.
- `scripts/export_port_schemas.py` — exports each Pydantic model as a
  JSON Schema file under `sos/contracts/ports/schemas/`. `--check`
  mode exits non-zero if the checked-in schemas drift from the source.
- `scripts/gen_ts_types.sh` — consumes the JSON Schemas via
  `json-schema-to-typescript` to produce a TS barrel for Inkwell.
- `Makefile` — `make contracts` / `make contracts-check` wrappers so
  CI and humans hit the same commands.
- `tests/contracts/test_port_schemas_export.py` — snapshot test that
  regenerating the schemas produces no diff (CI guard).

### Added — microkernel hardening (this session)
- `.gitignore` grew: per-machine agent-CLI state (`.cursor/`,
  `.claude/`, `.opencode.json`, …), `sos/bus/tokens.json.backup*`,
  generated graph artifacts (`docs/task-graph.*`). Stops these from
  drifting into commits.
- `scripts/tenant-setup.sh` — moved out of `sos/cli/` (where it
  masqueraded as user-facing pairing) into top-level `scripts/`
  (where ops-provisioning belongs). `sos/cli/pair-agent.sh` comment
  updated; `sos.services.billing.provision.TENANT_SETUP_SCRIPT` path
  fixed to match.
- `.github/workflows/ci.yml` — dropped dead imports (outreach,
  langgraph, crewai adapters no longer exist) and fixed a pre-existing
  `SOSBaseAdapter` typo (the real class is `AgentAdapter`). CI smoke
  is now green against the microkernel.
- pyproject.toml — R7 "ports are consumed, not re-implemented"
  contract placeholder added with a plan pointer; lands live in
  v0.9.1 (Phase 2).
- Product/tenant code extracted to `/tmp/sos-extracts/` for later
  porting into its proper repos: `agents/social.py` → mumega.com,
  `agents/dandan/` → agency, `services/content/daily_blog.py` →
  mumega-marketing, `services/outreach/` → outreach-engine,
  `skills/video-pipeline/` → TROP seed. Dead-code drops:
  `adapters/crewai`, `adapters/langgraph`, `adapters/discord/` (dir
  was shadowing the tracked `discord.py`), `kernel/examples/`,
  `agents/codex-mac-setup.md`.

### Docs
- `docs/plans/2026-04-19-mumega-mothership.md` — the plan that scopes
  Phases 1 through 8 (v0.9.0 → v1.0.0).

### Verify
- `pytest tests/contracts/` → 457 passed.
- `lint-imports` → 4/4 KEPT (R1, R2, R5, R6). R7 pending Phase 2.

## [0.8.2] — 2026-04-19 — Extract TROP from SOS

### Thesis
v0.8.1 leaked tenant-specific code into the platform. This release moves
TROP's seeds + standing workflows out of SOS into the TROP product repo
(`therealmofpatterns/sos-seed/`), and makes the pulse tenant-agnostic.

### Removed
- `sos/agents/trop/` — moved to `therealmofpatterns/sos-seed/trop/`. The
  TROP product repo now owns its AgentCard seeds and workflow catalog.

### Changed
- `sos/services/operations/pulse.py::load_standing_workflows` no longer
  branches on `project == "trop"`. It reads a JSON array from a file path
  resolved via (in order):
  1. explicit `workflows_file=` argument,
  2. `SOS_PULSE_WORKFLOWS_FILE_<PROJECT>` env var,
  3. `SOS_PULSE_WORKFLOWS_FILE` env var.
  Missing / unreadable file → returns `[]` (fail-soft). SOS has zero
  knowledge of tenant-specific workflow lists.
- `pulse.post_daily_rhythm` / `post_noon_pulse` / `post_evening_pulse`
  gained `workflows=` and `workflows_file=` kwargs. Tests pass workflows
  via kwarg; the CLI passes `--workflows-file PATH`.
- `sos/services/operations/pulse.py` CLI: new `--workflows-file` flag.

### Handoff — TROP product repo (`therealmofpatterns/sos-seed/`)
- `trop/seeds.py` — `python -m trop.seeds` registers the 4 standing cards
  against the SOS registry service.
- `trop/workflows.py` — in-memory catalog + `write_json()` helper to
  regenerate the canonical JSON file.
- `standing_workflows.json` — the file SOS's pulse reads.
- Operators point the organism at this file via
  `SOS_PULSE_WORKFLOWS_FILE_TROP=/home/mumega/therealmofpatterns/sos-seed/standing_workflows.json`.

### Tests
- `tests/services/test_pulse.py` — 5 new tests for the file-based loader
  (empty default, explicit path, per-project env, missing file, bad JSON
  shape). Existing tests pass workflows via `workflows=` kwarg.

## [0.8.1] — 2026-04-19 — TROP-ready

### Thesis
SOS as the hub/junction for a real online-business tenant. Closes the
ToRivers marketplace loop on the v0.8.0 objectives primitive; ships
the minimum auto-improve substrate (RAG-only, no fine-tune); gives
operators per-tenant visibility + agent kill-switch.

### Added
- **S1** `sos/adapters/torivers/bridge.py` migrated to `AsyncObjectivesClient` — marketplace workflows now post objectives with $MIND bounties, poll to completion, return artifact.
- **S2** First-class TROP tenant — 4 standing agent cards (`trop-social|content|outreach|analytics`), daily pulse scaffolding, capability-match runbook.
- **S3** `Objective.outcome_score: float | None` (range 0.0-1.0). Ack route accepts score in body. Audit events carry it. Payout gate unchanged — binary for v0.8.1.
- **S4** `sos/kernel/demo_bank.py` (fetch_winners, build_few_shot_prompt) + `sos/agents/curator.py` (standing agent claims `kind:harvest-winners`, writes top-decile to memory for RAG). Opt-in via `build_few_shot_prompt` at agent claim time.
- **S5** `sos/services/dashboard/` on port 6069 — per-tenant summary/agents JSON + admin-only kill switch. `sos/kernel/kill_switch.py` fail-soft read helper. CF dashboard UI lives in Mumega (documented hand-off).
- **S6** `sos/services/operations/organism.py` — finished daily-heartbeat loop. Three scheduled pulses per project (morning/noon/evening) with per-window 25h dedupe. Postmortem objective auto-posted per paid root. Systemd runbook at `docs/runbooks/sos-organism.service.md`.

### Changed
- `sos.services.dashboard` added to R1 import-linter module list. One R1 ignore added: `sos.services.dashboard.operator_api -> sos.services.registry` (RegistryClient has no `list_cards` yet; replace when it lands).
- `objective.schema.json` regenerated to include `outcome_score` property.

### Mumega hand-offs (documented, not built)
- Customer UI at `app.mumega.com/dashboard/{project}` consuming the SOS dashboard API.
- Signup UI POSTing to `/saas/tenants`.
- SSE proxy for `/dashboard/stream` (future — v0.8.1 exposes polling only).

### TROP hand-offs (documented, not built)
- ToRivers customer-facing site calling `POST /adapters/torivers/execute`.
- Wallet debit on bridge `status: completed`.
- Analytics agent POSTing `/objectives/{id}/ack` with `outcome_score`.

### Non-goals (deferred)
- v0.8.2 — decay sweeper (stalled claims), subscription-by-subtree push.
- v0.8.3 — fine-tune orchestrator (curator is RAG only in v0.8.1).
- Bedrock provider adapter (explicitly dropped per 2026-04-19 user direction).
- Mumega dashboard UI, ToRivers customer site (other repos).

## [0.7.3] - 2026-04-18 — AgentCard self-register + heartbeat helper

**Release theme: "Close the v0.7.2 loop so the endpoint actually returns data."**

v0.7.2 exposed a read surface for AgentCards but nothing wrote them, so
`GET /agents/cards` returned empty. v0.7.3 adds the write side: a POST
endpoint on the registry and a fail-soft kernel helper agents call from
their boot + heartbeat path.

### Added

- `sos/services/registry/app.py`:
  - `POST /agents/cards` — upserts an `AgentCard` into
    `sos:cards[:<project>]:<name>` with a configurable TTL. Bearer
    auth, `registry:card_write` gate action.
  - Scope enforcement: a scoped token cannot write a card whose
    `card.project` mismatches its own scope (403). System/admin
    tokens bypass. Malformed payloads return 422.
- `sos/kernel/heartbeat.py` — new primitive:
  - `emit_card(card, *, project=None, ttl_seconds=300, base_url=None,
    token=None, timeout_s=5.0)` — thin `httpx.post` wrapper that
    reads `SOS_REGISTRY_URL` / `SOS_REGISTRY_TOKEN` /
    `SOS_SYSTEM_TOKEN` from env. Returns `True`/`False` and never
    raises — a dead registry must not crash a working agent.
  - No token → no network call, return `False`.
- Tests:
  - `tests/services/test_registry_cards.py` — 4 new POST tests (401
    without bearer, 422 on invalid payload, happy-path system write,
    403 on cross-project scope mismatch).
  - `tests/kernel/test_heartbeat.py` — 6 new tests (no-token
    short-circuit, happy path captures URL/params/headers/JSON,
    project-from-card, explicit project wins, non-2xx → False,
    exception → False).

### Redis

- Write path: `POST /agents/cards` → `write_card` →
  `HSET sos:cards[:<project>]:<name>` with `EXPIRE ttl_seconds`.
- Suggested heartbeat cadence: every 60s with `ttl_seconds=300`
  (3× margin for network blips).

### Verified

- 19/19 new tests green (13 card route tests + 6 heartbeat tests).
- Full registry regression: 44 passed + 5 skipped across
  `tests/services/test_registry*.py`, `tests/contracts/test_agent_card.py`,
  and `tests/kernel/test_heartbeat.py`.
- `.venv/bin/lint-imports` green (4 contracts kept). Kernel→contracts
  is clean; `sos.kernel.heartbeat` imports only
  `sos.contracts.agent_card`, not the registry service.

---

## [0.7.2] - 2026-04-18 — AgentCard registry surface

**Release theme: "Inkwell (and anyone else) can now ask *which agent is live right now.*"**

Exposes the runtime AgentCard overlay (session/pid/host/warm_policy/
last_seen/cache_ttl_s/plan) as a first-class HTTP surface on the
registry, parallel to the existing soul-level `/agents` endpoint. The
`AgentCard` Pydantic contract + JSON Schema have existed since v0.4;
v0.7.2 wires Redis read/write helpers and the HTTP routes that let
operator UIs consume them.

### Added

- `sos/services/registry/__init__.py`:
  - `read_all_cards(project)` — scan `sos:cards[:<project>]:*`, return
    parsed `AgentCard` list. Fail-soft (`[]` on Redis miss) to match
    `read_all`.
  - `read_card(agent_name, project)` — single-card lookup, strips
    `agent:` prefix for convenience.
  - `write_card(card, project, ttl_seconds=300)` — HSET the card's
    flat Redis hash under `sos:cards[:<project>]:<name>` with a
    heartbeat-style TTL so dead agents expire on their own.
- `sos/services/registry/app.py`:
  - `GET /agents/cards?project=<slug>` — Bearer auth,
    `registry:cards_list` gate action, reuses
    `_resolve_project_scope` so scoped tokens are forced to their own
    project.
  - `GET /agents/cards/{agent_name}` — 404 when no card is
    registered, 403 on cross-project scope mismatch.
  - Both routes are declared *before* `/agents/{agent_id}` so
    `cards` is never captured as an agent_id path param.
- Tests: `tests/services/test_registry_cards.py` — 9 tests covering
  401/empty/full-roundtrip/404/cross-project-403/scope-forced-to-own-
  project/route-ordering-regression-guard.

### Redis key format

- `sos:cards:<agent_name>` (no project)
- `sos:cards:<project>:<agent_name>` (project-scoped)

Cards never collide with `sos:registry:` identity hashes.

### Verified

- 9/9 new tests green.
- Existing registry regression: 25 passed + 5 skipped in
  `tests/services/test_registry*.py` + `tests/contracts/test_agent_card.py`.
- `.venv/bin/lint-imports` green (4 contracts kept).

---

## [0.7.1] - 2026-04-18 — /sos/traces HTML UI

**Release theme: "Read one trace without `jq`."**

Adds operator-facing HTML renders over the existing `/sos/traces` JSON
contract. The v0.6.0 trace index already groups audit events by
`trace_id`; v0.7.1 gives it eyes. Same auth + 4xx semantics, same disk
sink is authoritative, no new surface beyond two HTML routes.

### Added

- `GET /sos/traces/html` — index view rendering the aggregated trace
  summary as a dark-themed dashboard. Summary cards (total traces,
  events, tenants, agents), sortable-by-time table, kind pills
  coloured by `AuditEventKind`. Each row's trace_id links to the
  detail page.
- `GET /sos/traces/{trace_id}/html` — per-trace detail view rendering
  events in chronological order. One row per event with
  timestamp / kind pill / agent+tenant / action / target / decision /
  cost / payload. Sanitised `inputs` / `outputs` / `metadata` appear as
  collapsible `<details>` blocks.
- `sos/services/dashboard/templates/traces.py` — inline HTML
  templates (same pattern as `templates/brain.py` and
  `templates/login.py`, no Jinja2 dep).
- Tests: `tests/services/test_dashboard_traces_html.py` — 5 smoke
  tests covering index content, empty-window fallback, detail render
  with chronological ordering, 404 on unknown trace, and 401 without
  bearer on both HTML routes.

### Changed

- `sos/services/dashboard/routes/traces.py` — extracted the
  aggregation loop into `_build_index(days, limit)` so the JSON and
  HTML index routes share one implementation. Route registration
  order: `/sos/traces/html` is declared *before*
  `/sos/traces/{trace_id}` so `html` isn't captured as a trace id.

### Verified

- 9/9 traces tests green (5 new HTML + 4 existing JSON).
- `.venv/bin/lint-imports` green (4 contracts kept).

---

## [0.7.0] - 2026-04-18 — Brain hardening: ProviderMatrix feedback + operator HTML

**Release theme: "Breakers that reflect real traffic, and a dashboard you can actually read."**

Hardens the provider fallback path introduced in v0.4.3 and surfaces
BrainService state to operators via an HTML render. No new services
— only primitives around existing `sos/providers/matrix.py` and a
second response mode on the existing `/sos/brain` route.

### Added

- `sos/providers/matrix.py`:
  - `select_with_fallback(matrix, tiers, healthy_only=True)` — lazy
    iterator that walks tier preference in order and skips open
    breakers at iteration time, so callers no longer reimplement
    fallback.
  - `call_with_breaker(card)` async context manager — records
    success/failure on the card's breaker from real adapter calls,
    raises `ProviderMatrixError` up front when the breaker is open.
  - `probe_provider(card, timeout=None)` — drives `health_probe_url`
    via httpx; 2xx closes the breaker, non-2xx/exception opens it,
    cards without a probe URL no-op without recording.
  - `reset_breakers()` — test helper.
- `GET /sos/brain/html` — operator-facing HTML render of the
  `BrainSnapshot` plus live ProviderMatrix breaker state. Auth +
  503 semantics match the JSON route. Fail-soft: if the matrix YAML
  is missing the page still renders with an empty-state row.
- `sos/services/dashboard/templates/brain.py` — inline HTML template
  matching the login.py pattern (no Jinja2 dep).
- Tests:
  - `tests/providers/test_matrix_hardening.py` — 12 tests covering
    breaker state transitions, probe 2xx/5xx/exception/no-url paths,
    and the three tier-walk invariants.
  - `tests/services/test_dashboard_brain_html.py` — 4 HTML smoke
    tests: snapshot render, provider row breaker classes, 503 on
    miss, 401 without bearer.

### Why this matters for federation

The dashboard now shows breaker state per provider card at a glance,
which is the precondition for any peer organism trusting this one's
self-reported health. v0.8.x spore federation will reuse
`call_with_breaker` + `probe_provider` to mark peer links unhealthy.

### Changed

- Moved `tests/contracts/test_alembic_orm_parity.py` (introduced in
  v0.6.3) to `tests/migrations/test_alembic_orm_parity.py`. The test
  legitimately imports `sos.services.squad.models` to inspect ORM
  columns, which violates R6 ("contract tests don't import
  services"). Relocating is the right fix — the test is a
  migration/ORM coherence check, not a contract.

### Verified

- 12/12 matrix hardening tests, 4/4 HTML smoke tests, 4/4 parity
  tests green.
- `.venv/bin/lint-imports` green (4 contracts kept, R6 restored).

---

## [0.6.3] - 2026-04-18 — Env hygiene + migration parity contract

**Release theme: "Your venv should be able to run your tests."**

Quality-of-life patch. Fixes the dev environment so a fresh
`uv sync --extra dev` produces a venv that can actually run the full
contract suite, and adds a regression contract that catches ORM/DB
drift at test time instead of migration time.

### Added

- `tests/contracts/test_alembic_orm_parity.py` — 4 invariant tests:
  for each squad table (`squad_skills`, `pipeline_specs`,
  `pipeline_runs`), apply `alembic upgrade head` against a fresh
  SQLite and assert `PRAGMA table_info` equals the ORM's
  `__table__.columns`. Fourth test guards against re-introducing the
  six columns dropped in v0.6.2.
- `alembic>=1.13`, `bcrypt>=4.0`, `jsonschema>=4.0` added to
  `[project.optional-dependencies.dev]`. They were transitively
  required by tests + the migration runner but never declared, so a
  fresh venv collapsed with `ModuleNotFoundError`.

### Verified

- `uv sync --extra dev` clean; `.venv/bin/lint-imports` green
  (4 contracts kept); `.venv/bin/pytest tests/contracts/ -q` green
  (436 passed, up from 432 with the 4 new parity tests).

---

## [0.6.2] - 2026-04-18 — Squad Schema Coherency

**Release theme: "One truth per column."**

Drops six columns from the squad baseline migration and ORM that had
no runtime readers or writers. They survived from pre-Alembic releases
whose original migration code was removed; v0.6.1's baseline kept them
for parity with a live DB that, on inspection, had never actually been
stamped (0001 was added fresh in `edbee488`). Fixing the baseline
in place is cleaner than a follow-up drop migration.

### Removed

- `squad_skills.framework` — framework wiring now lives in `sos/adapters/*`
  (crewai, langgraph, torivers, discord) rather than on the skill row.
- `squad_skills.agent` — skills are dispatched dynamically via
  `squads.members_json` + `roles_json`, not pinned to a named agent.
- `pipeline_specs.review_enabled` / `review_agent` / `review_cmd` — review
  is now a first-class gate signal on `sos/kernel/policy/gate.py`
  (`can_execute()` + arbitration from v0.5.2).
- `pipeline_runs.reviewer_notes` — review verdicts are emitted as audit
  events tagged with `trace_id` (see v0.5.6 audit stream).

### Changed

- `sos/services/squad/alembic/versions/0001_initial.py` — `create_table`
  calls for `squad_skills`, `pipeline_specs`, and `pipeline_runs` no longer
  include the six legacy columns. Module docstring updated to explain
  the coherency-first decision.
- `sos/services/squad/models.py::SquadSkill`, `PipelineSpec`, `PipelineRun`
  — matching column definitions removed.

### Test state

Verified with a fresh SQLite roundtrip: `alembic upgrade head` applied
cleanly and `PRAGMA table_info` on all three tables returns exactly the
column set defined in the ORM. 432 contract tests still pass; 8 squad
decoupling tests + 17 economy usage tests (v0.6.1 regression bar) green.

### Deployment note

Any existing squad SQLite that was manually created via prior code
still carries the six columns. They're harmless (defaults make them
invisible to ORM selects), but can be dropped manually with
`ALTER TABLE … DROP COLUMN` on Postgres or a VACUUM INTO rebuild on
SQLite when convenient.

---

## [0.6.1] - 2026-04-18 — Test-Debt Cleanup (Fork A, milestone 1)

**Release theme: "Honor what the token actually claims."**

Closes the six pre-existing 403 Forbidden failures across
`tests/services/test_settlement.py` and `tests/services/test_economy_usage.py`
that slipped in when the v0.5.x policy-gate and audit-wrapper waves landed
on the economy service. See `docs/plans/2026-04-18-v0.6.1-test-debt.md`.

### Fixed

- `sos/kernel/auth.py::_entry_to_ctx` now honors `is_admin` and `is_system`
  boolean fields on a `tokens.json` entry. Previously these fields were
  silently ignored — admin status only flowed from the hardcoded
  `_ADMIN_AGENTS` list and no JSON path could mark a token as system-scoped.
  Production `sos/bus/tokens.json` entries still use the `agent`-based path
  unchanged; the new fields are additive.
- `sos/services/economy/app.py::list_usage` (`GET /usage`) now defaults
  the gate's tenant scope to the caller's own project when no `tenant`
  query parameter is supplied. Previously the route pinned `gate_tenant`
  to `"mumega"`, which caused the gate to 403 every project-scoped caller
  that didn't pass an explicit filter. Project-scoped callers without a
  filter now also see only their own events, matching the docstring.

### Added

- `tests/contracts/test_token_scope_fields.py` — four regression tests that
  pin the `is_admin` / `is_system` / project-scope / agent-admin behavior
  in `_entry_to_ctx` so this can't re-regress silently.
- `docs/plans/2026-04-18-v0.6.1-test-debt.md` — sprint plan for the cleanup.
- `docs/SUBAGENT-BRIEF.md` — the first-read brief every subagent (Sonnet,
  Haiku, or specialist) now loads. Captures the microkernel + mycelium
  invariants (RPi-first reference impl, substrate-agnostic kernel,
  import-linter contracts are load-bearing, dispatcher is protocol not
  version, federation is a first-class concern).

### Test state

All 6 targeted failures now pass (30/30 in settlement + economy_usage
modules). 432 contract tests remain green (R0/R1/R2/R5/R6 AST sweeps
still enforce the kernel/services/clients boundary). 33 unrelated
pre-existing failures across memory, tools, autonomy, catalog, and
identity modules remain on the v0.6.x test-debt list — they are not
regressions from v0.6.1 and will be addressed in later patches.

---

## [0.6.0] - 2026-04-18 — Stability Polish (observability + schema + config)

**Release theme: "Turn the knobs up: traces you can see, migrations you can replay, config you can type-check."**

Closes Step 2 of the two-step stability plan
(`docs/plans/2026-04-18-sos-stability-two-steps.md`). Four concerns:

1. **OpenTelemetry bridge** — a single `sos.kernel.telemetry` module
   bootstraps OTEL per service and bridges the v0.5.7 `trace_id`
   contextvar into OTEL's `SpanContext`, so the same trace stitches
   across HTTP hops, bus envelopes, and audit writes without
   per-service glue.
2. **Alembic migrations** — squad + identity schemas move out of
   implicit `CREATE TABLE IF NOT EXISTS` at boot into versioned
   revisions with a one-liner `scripts/migrate-db.sh` runner.
3. **Typed kernel config** — `sos.kernel.settings` consolidates Redis,
   service URLs, audit, gateway, feature flags, integrations, and
   auth-gateway settings into seven `pydantic-settings` classes with
   an LRU-cached `get_settings()`.
4. **`/sos/traces` dashboard route** — disk audit log aggregated by
   `trace_id`, giving operators a per-trace summary index and detail
   view without leaving the dashboard.

### Added — OpenTelemetry

- `sos/kernel/telemetry.py` — `init_tracing(service_name)`,
  `instrument_fastapi(app)`, `adopt_current_trace_id_as_otel_parent()`,
  and `span_under_current_trace(name)` context manager. Full no-op
  when neither `OTEL_EXPORTER_OTLP_ENDPOINT` nor `SOS_OTEL_CONSOLE=1`
  is set — dev/test runs stay quiet by default. OTLP HTTP exporter +
  console fallback + auto-instrumentation for FastAPI, httpx, redis.
- `sos/services/{saas,brain,squad,engine,tools,economy,identity,integrations,registry,journeys,operations,dashboard,billing,memory,content}/app.py` —
  wired to `init_tracing(<service>)` + `instrument_fastapi(app)` at
  startup.
- `sos/services/brain/service.py` — bus handler wrapped with
  `span_under_current_trace("bus.handle.<stream>")` so every event
  shows up as one span on the inbound trace.
- `tests/test_kernel_telemetry.py` — 6 unit tests covering the no-op
  path, console flag, `SpanContext.trace_id` matching, and the
  no-active-trace pass-through.
- `pyproject.toml` — new `telemetry` extra (OTEL SDK + OTLP HTTP +
  fastapi/httpx/redis instrumentation); same six packages folded into
  the existing `full` extra.

### Added — Alembic migrations

- `sos/services/squad/models.py` — 11 tables, SQLAlchemy 2.0
  declarative.
- `sos/services/identity/models.py` — 5 tables.
- `sos/services/{squad,identity}/alembic.ini` + `alembic/env.py` +
  `alembic/versions/0001_initial.py` — baseline revisions, parity
  verified byte-for-byte against live on-disk DBs via
  `PRAGMA table_info` + `sqlite_master.sql`.
- `scripts/migrate-db.sh` — one-liner `alembic upgrade head` runner
  for both services.
- `alembic>=1.13` and `sqlalchemy>=2.0` promoted to core deps.

### Added — Typed config

- `sos/kernel/settings.py` — `RedisSettings`, `ServiceURLSettings`,
  `AuditSettings`, `GatewaySettings`, `FeatureFlags`,
  `IntegrationSettings`, `AuthGatewaySettings` (7 classes,
  `pydantic-settings` + `SecretStr`). `get_settings()` is LRU-cached;
  `reload_settings()` clears the cache for tests.

### Added — `/sos/traces` dashboard

- `sos/contracts/traces.py` — `TraceSummary`, `TraceIndexResponse`,
  `TraceDetailResponse`.
- `sos/services/dashboard/routes/traces.py` — `GET /sos/traces` (list
  aggregated by `trace_id`, bounded to last N days + first M traces)
  and `GET /sos/traces/{trace_id}` (oldest-first event detail). Reads
  the authoritative disk audit log at
  `~/.sos/audit/{tenant}/{YYYY-MM-DD}.jsonl` — Redis is observational
  and skipped here.
- `tests/services/test_dashboard_traces_route.py` — 4 tests covering
  aggregation, detail ordering, unknown-trace 404, and bearer auth.

### Fixed

- `tests/test_identity.py` — Alembic migration removed the boot-time
  `CREATE TABLE`; test now runs `alembic upgrade head` against a tmp
  DB explicitly (mirrors what `scripts/migrate-db.sh` does in prod),
  instead of relying on pre-existing schema in the shared
  `~/.sos/data/identity.db`.

---

## [0.5.7] - 2026-04-18 — Stability MVP (trace propagation + idempotency)

**Release theme: "The spine keeps an unbroken thread; write endpoints stop double-billing."**

Closes Step 1 of the two-step stability plan
(`docs/plans/2026-04-18-sos-stability-two-steps.md`). Three concerns in
one cut:

1. **W3C-style `trace_id` propagation** end-to-end through the Redis bus
   and the audit spine, so a single request can be stitched across
   services and kernel events without changing function signatures.
2. **Canonical idempotency helper** + wrapper applied to 12 write
   endpoints across economy / squad / engine / tools / saas — replay a
   request with the same `Idempotency-Key` and get the cached response;
   replay with a mismatched body and get a deterministic 409.
3. **Pre-gate regression tests** (integrations + identity) and
   **AsyncRegistryClient** unit tests, closing the test debts that the
   v0.5.6.1 hotfix left open.

### Added — `trace_id` propagation

- `sos/contracts/messages.py` — `BusMessage.trace_id: Optional[str]`
  (32 hex chars) + `BusMessage.new_trace_id()`. All 12 v1 envelope
  JSON schemas (`sos/contracts/schemas/messages/*.json`) carry the
  optional property.
- `sos/kernel/trace_context.py` — `ContextVar`-based current-trace
  holder + `use_trace_id()` async-safe context manager. Cheap, opt-in,
  no signature churn.
- `sos/kernel/audit.py` — `new_event()` reads the current `trace_id`
  from the contextvar when the caller doesn't supply one, so every
  kernel audit event produced inside a bus handler inherits the
  inbound envelope's trace.
- `sos/services/brain/service.py` — consumer sets the contextvar on
  `_handle_event`; `task.scored` and `task.routed` emissions carry the
  inbound `trace_id` (or mint one if the upstream didn't).

### Added — idempotency

- `sos/kernel/idempotency.py` — `async with_idempotency(*, key, tenant,
  request_body, fn, ttl_s=86400, redis=None)`. Key shape
  `sos:idem:<tenant|_system>:<key>`, fingerprint
  `sha256(json.dumps(body, sort_keys=True, default=str))`. Hit replays
  the cached response, miss runs `fn` and caches, fingerprint mismatch
  raises `HTTPException(409)`, `None` key bypasses. Lazy imports keep
  Redis optional at import-time.

### Modified — 12 endpoints now accept `Idempotency-Key: <str>` header

- `sos/services/economy/app.py` — `POST /credit`, `POST /debit`
- `sos/services/tools/app.py` — `POST /execute`
- `sos/services/engine/app.py` — `POST /tasks/create`
- `sos/services/squad/app.py` — `POST /tasks`, `POST /tasks/{id}/complete`
- `sos/services/saas/app.py` — `POST /tenants`,
  `POST /tenants/{slug}/seats`, `POST /onboard`,
  `POST /builds/enqueue/{slug}`, `POST /signup`, `POST /my/tasks`

### Added — test debts closed

- `tests/services/test_integrations_pregate_audit.py` (3 tests) —
  pre-gate DENY audit assertions.
- `tests/services/test_identity_pregate_audit.py` (6 tests) — pre-gate
  DENY audit assertions for `/avatar/generate` and
  `/avatar/social/on_alpha_drift`.
- `tests/clients/test_registry_client.py` (6 tests) —
  `AsyncRegistryClient.list_agents()` HTTP contract via
  `httpx.MockTransport`.
- `tests/kernel/test_idempotency.py` (6 tests) — helper contract:
  hit/miss/mismatch/None-key/cross-tenant-isolation.

### Fixed

- `tests/contracts/test_messages_integration.py` — fossilised
  `assert len(_SCHEMA_FILES) == 11` replaced with a dynamic check
  derived from `typing.get_args(MessageType)` (12 schemas exist today).

### Verification

- Full `pytest -q` sweep: 1209 passed, 15 skipped. 34 failing / 17
  erroring tests are all pre-existing fossils (economy usage fixtures,
  autonomy coordinator imports, tools-service health-shape drift) —
  baseline on the same commit shows 42 failed + 24 errors, so this
  sprint *reduced* the failure count by 15 and introduced zero new
  regressions.

### Follow-up — Step 2 (v0.6.0)

Tracked in `docs/plans/2026-04-18-sos-stability-two-steps.md`: OTEL
instrumentation, Alembic for the one stateful service that still needs
it, typed config consolidation, `/sos/traces` viewer over the audit
stream. Cloudflare layer starts after Step 2.

## [0.5.6.1] - 2026-04-18 — Identity pre-gate audit gap

**Hotfix** found by runtime smoke of the v0.5.0→v0.5.6 arc.

`identity/app.py` peeks at the bearer token *before* calling
`can_execute()` because it uses the caller's own tenant as the gate's
`tenant` arg (any-valid-scope semantics). The three pre-gate deny paths
— missing bearer, invalid token, scopeless-but-verified token — raised
401/403 without writing to the audit spine.

### Added (`sos/services/identity/app.py`)

- `_emit_identity_deny(action, target, reason, tenant="unknown")` — async
  helper around `kernel.audit.append_event` with
  `policy_tier="identity_pregate"`. Try/except-wrapped.

### Modified

Both `/avatar/generate` and `/avatar/social/on_alpha_drift` emit a
`POLICY_DECISION` DENY event on each pre-gate rejection before the
existing 401/403. Any-valid-scope semantics preserved
(`test_avatar_generate_403_on_scopeless_token` still green).

### Verification

- `pytest tests/services/test_identity_avatar_endpoints.py` — 6/6 green.
- Inline smoke confirmed 3 `identity_pregate` DENY events on a fresh run.

### Follow-up

`integrations/app.py` has the same pre-gate pattern and the same gap
class — tracked separately; out of scope for this hotfix.

## [0.5.6] - 2026-04-18 — Gateway bridge audit-wrapper

**Release theme: "The external front door joins the spine."**

`sos/services/gateway/bridge.py` is the public entry for external agents
(ChatGPT, Claude, custom integrations). Its auth table lives in
`~/.sos/data/gateway/tenants.json` — a flat registry separate from the
kernel's `tokens.json`, populated by self-service `POST /register` calls.
There is also a `MUMEGA_INTERNAL_KEY` env bypass that returns
`tenant_id="mumega"`.

v0.5.6 wraps the existing `require_tenant` dependency with
`POLICY_DECISION` audit emission so every external-agent call lands on
the unified kernel spine. No route-decorator changes, no re-registration
of external keys in kernel auth.

### Added (`sos/services/gateway/bridge.py`)

- `_emit_gateway_policy(agent, action, target, tenant, allowed, reason)` —
  async helper around `kernel.audit.append_event` + `new_event` with
  `policy_tier="gateway_bridge"`. Try/except-wrapped so audit outages
  never block a request.

### Modified `require_tenant`

All four code paths now audit:

- Missing API key (no `Authorization: Bearer` and no `X-API-Key`) →
  emit deny `missing_api_key`, raise 401.
- Internal-key bypass (`MUMEGA_INTERNAL_KEY`) → emit allow `internal_key`
  with `agent="internal"`, `tenant="mumega"`, return `"mumega"`.
- Invalid API key → emit deny `invalid_api_key`, raise 401.
- Valid tenant key → emit allow `tenant_key` with
  `agent=tenant["id"]`, return the tenant id.

`action` is uniformly `gateway:request` — the dep doesn't know which
route it's guarding. Operators reading the audit stream see one event
per authenticated external-agent call, with `tenant` indicating *who* and
the access log indicating *where*.

### Routes covered (6)

All callers of `Depends(require_tenant)`:
`GET /manifest`, `POST /chat`, `POST /tasks/create`, `POST /memory/store`,
`POST /memory/search`, `GET /system/status`.

Public routes (`GET /`, `POST /register`) are untouched.

### Proof

- `python -c "from sos.services.gateway.bridge import app, require_tenant, _emit_gateway_policy"` — module imports clean.
- `pytest tests/kernel/ tests/contracts/` — 447/451 passing (4 failures
  are pre-existing schema-count drift in `test_messages_integration.py`,
  unrelated to this sprint).

### Arc complete

v0.5.0 → v0.5.6 closes the "kernel enforces the blueprint" arc:

- **v0.5.0** — R0 floor + disk-authoritative audit stream.
- **v0.5.1** — Unified `can_execute()` gate.
- **v0.5.2** — Arbitration (intent → proposal → ratification, read-over-audit).
- **v0.5.3** — Gate wave 1: economy, registry, identity, journeys, operations (15 routes).
- **v0.5.4** — saas audit-wrapper (40 routes).
- **v0.5.5** — squad in-dep audit (26 routes).
- **v0.5.6** — gateway/bridge in-dep audit (6 routes).

Every authenticated route in every SOS service now writes one
`POLICY_DECISION` audit event per call on the unified kernel spine:
6 services via `can_execute()`, 3 services via native-auth wrappers. The
audit spine is load-bearing. The gate contract is frozen. New signals
compose *inside* `can_execute()`; new event kinds add to
`AuditEventKind`. Kernel shape holds.

---

## [0.5.5] - 2026-04-18 — Squad audit (in-dep)

**Release theme: "Squad's capability check joins the spine."**

Squad already does the strongest per-service auth in the codebase: every
protected route calls `require_capability(resource, operation)` which
resolves a `sos.kernel.capability.Capability` via bcrypt/sqlite token
lookup and runs `verify_capability(capability, action, resource)` — a
signed-capability model richer than the kernel gate's generic pillars.
What was missing: those decisions never showed up in the unified audit
log.

v0.5.5 adds audit emission at the only place it belongs — inside
`require_capability` itself, so **no route decorator changes** and the
capability enforcement stays exactly where it was.

### Added (`sos/services/squad/auth.py`)

- `_emit_squad_policy(agent, action, target, tenant, allowed, reason)` —
  async helper around `kernel.audit.append_event` + `new_event` with
  `policy_tier="squad_capability"`. Wraps the call in try/except so
  audit hiccups never break a request.

### Modified `require_capability`

The inner `dependency` coroutine now emits one `POLICY_DECISION` event
on every code path:

- Missing bearer → emit deny (`missing_authorization`), raise 401.
- Invalid token → emit deny (`invalid_token`), raise 401.
- System token → emit allow (`system_token`), return auth.
- Capability denial → emit deny (reason from `verify_capability`), raise 403.
- Capability allow → emit allow (`capability_ok`), return auth.

`action` on the audit event is formatted as `squad:<resource>_<operation>`
(e.g. `squad:tasks_write`, `squad:squads_read`). `tenant` is the
authenticated `tenant_id` on allow (or `"mumega"` for system), `"unknown"`
on pre-auth denials.

### Routes covered (26)

All callers of `Depends(require_capability(...))` in `sos/services/squad/app.py`
benefit transparently:

- squads (write/read) — create, list, read, update, delete
- tasks (write/read) — create, list, read, update, close, reassign
- skills (register/read/execute) — register, list, execute
- state (read/write), pipeline (read/write/execute)

### Why not `can_execute()` directly

`can_execute()` accepts a `capability=` kwarg but only activates it under
`SOS_REQUIRE_CAPABILITIES=1`. Squad's native check is always on and runs
the signed `verify_capability` logic — stronger than the gate's default.
Re-routing squad through the gate would either weaken the check (flag
off) or duplicate it (flag on). The in-dep audit emission gives us full
observability on the unified spine without touching the enforcement
semantics.

### Proof

- `pytest tests/test_squad_runtime.py tests/contracts/test_squad_task.py tests/clients/test_squad_client.py`
  — 70/70 green, no regressions.
- Zero changes to `sos/services/squad/app.py` — all 26 route decorators
  unchanged.
- `verify_capability` still runs at the same call site with the same
  arguments. Enforcement logic untouched.

### Deferred to v0.5.6

- **v0.5.6** — `sos/services/gateway/bridge.py` (external-agent API keys,
  independent tenant registry). Same audit-wrapper pattern as saas.

---

## [0.5.4] - 2026-04-18 — SaaS audit-wrapper

**Release theme: "The audit spine reaches the services the gate cannot."**

v0.5.3 generalized `can_execute()` across every service whose auth fit the
kernel gate's 5-pillar model. `saas` doesn't — it runs two orthogonal auth
systems (master-key admin + tokens.json-hash customer lookup with sqlite
fallback) that the gate was never designed for. Rather than cram the saas
tables into kernel auth (duplication), v0.5.4 wraps the native auth deps
with audit emission so every authenticated saas call writes one
`POLICY_DECISION` event on the kernel spine.

### Added (`sos/services/saas/app.py`)

- `_emit_policy(agent, action, target, tenant, allowed, reason, tier)` —
  async helper around `kernel.audit.append_event` + `new_event`. Wraps the
  call in try/except at debug so audit failures never break a request.
- `audited_admin(action: str)` — FastAPI dep factory. Wraps `require_admin`;
  emits `POLICY_DECISION` with `policy_tier="saas_admin"` on both allow
  (agent="admin", reason="master_key") and deny (agent="anonymous", reason
  from HTTPException detail). Re-raises on deny.
- `audited_customer(action: str)` — mirror of above for `require_customer`;
  `policy_tier="saas_customer"`. Preserves the `-> str` (tenant_slug)
  return contract so existing route bodies are unchanged.

### Routes migrated (40)

- **Admin (29):** every `_: None = Depends(require_admin)` now reads
  `_: None = Depends(audited_admin("saas:<action>"))`. Actions cover
  tenants (create/list/read/update/activate/suspend), seats, billing/usage,
  rate-limit, marketplace, notifications, onboarding, builds, domains.
- **Customer (11):** every `tenant_slug: str = Depends(require_customer)`
  now reads `tenant_slug: str = Depends(audited_customer("saas:<action>"))`.
  Covers my_connect/dashboard/wallet/transactions/tasks/squads/activity/
  invite/chat.

Public/webhook routes (`/`, `/health`, `/auth/*`, `/webhooks/*`, `/signup`,
`/billing/webhook`, `/resolve/{hostname}`) are untouched — they have their
own signature/IP verification.

### Why not `can_execute()`

`saas` admin is an env-secret (`MUMEGA_MASTER_KEY`), not a token row.
`saas` customer auth hashes `sk-{slug}-{hex}` into `tokens.json` with a
sqlite `bus_token` fallback. Forcing these through the gate would either
register every secret in kernel auth (two sources of truth) or short-
circuit the gate's first pillar (defeats the point). Audit-wrapper is the
honest middle path: keep the service-native auth, add the kernel-
observable record.

### Proof

- `pytest tests/test_saas_api.py` — 9/9 green, no regressions.
- `require_admin` / `require_customer` still exist at the exact same call
  sites inside the new factories — the real auth work is unchanged.
- Every authenticated route emits one `POLICY_DECISION` event per call
  (allow or deny path).

### Deferred to v0.5.5 + v0.5.6

- **v0.5.5** — `sos/services/squad/*`. Squad's auth fits the gate's
  existing `capability` parameter; direct `can_execute` integration.
- **v0.5.6** — `sos/services/gateway/bridge.py`. External-agent API keys;
  same audit-wrapper pattern as saas.

---

## [0.5.3] - 2026-04-18 — Gate mop-up, wave 1

**Release theme: "The gate's reach expands."**

v0.5.1 shipped `can_execute()` + one POC migration (`integrations`). v0.5.3
generalizes the gate across 5 more services. Every authenticated route on
these services now calls the unified gate and writes exactly one
`POLICY_DECISION` audit event — no more service-local `_verify_bearer`,
`_check_scope`, or `_require_admin` reimplementations.

### Services migrated (15 routes, all in parallel)

- **`sos/services/economy/app.py`** (4 routes): `GET /budget/can-spend`,
  `POST /usage`, `GET /usage`, `POST /settle/{usage_event_id}` (admin-only).
  Removed `_verify_bearer`, `_resolve_tenant`, `_auth_ctx_to_entry` helpers.
- **`sos/services/registry/app.py`** (2 routes): `GET /agents`,
  `GET /agents/{id}`. `_resolve_project_scope` retained — handles
  sub-tenant project filtering the gate intentionally doesn't cover.
- **`sos/services/identity/app.py`** (2 routes): `POST /avatar/generate`,
  `POST /avatar/social/on_alpha_drift`. Scopeless-but-verified tokens now
  short-circuit with a 403 before the gate runs (preserves pre-migration
  behaviour).
- **`sos/services/journeys/app.py`** (4 routes): `GET /recommend/{agent}`,
  `POST /start`, `GET /status/{agent}`, `GET /leaderboard`. All admin-only —
  use `_raise_on_deny(decision, require_system=True)`.
- **`sos/services/operations/app.py`** (3 routes): `POST /run`,
  `GET /templates`, `GET /templates/{product}`. All admin-only.

### Pattern

Every migrated route follows the v0.5.1 POC pattern verbatim:

```python
decision = await can_execute(
    action="<service>:<verb>",
    resource=<resource_id>,
    tenant=<tenant>,
    authorization=authorization,
)
_raise_on_deny(decision, require_system=<True for admin-only>)
```

`_raise_on_deny()` maps denial reason to 401 (bearer/auth) vs 403 (scope/admin)
and enforces `require_system` post-allow. Every service carries a verbatim
copy of the helper — small enough that a shared module would be premature
abstraction.

### Proof

- All `tests/kernel/` + `tests/contracts/` pass (442 tests, 0 failures).
- Per-service test suites pass where they were passing on v0.5.2:
  registry 21/21, journeys 14/14, operations 7/7, identity avatars all
  green. Pre-existing failures in `test_economy_usage.py`,
  `test_auth_migration.py`, `test_settlement.py` (all `base58` /
  sqlite / brain-registry issues unrelated to the kernel) are unchanged.
- `lint-imports`: 4 contracts kept, 0 broken.

### Deferred

- **v0.5.4:** `sos/services/saas/app.py` — 39 routes, custom slug-based
  scoping. Needs a deliberate pass, not a parallel sprint.
- **v0.5.5:** `sos/services/squad/` — 28 routes + a capability→gate bridge
  that still wants a design doc before code.
- **v0.5.6+:** `sos/services/gateway/bridge.py` and any remaining sites.

## [0.5.2] - 2026-04-18 — Arbitration: intent → proposal → ratification

**Release theme: "The kernel enforces the blueprint — step 3 of 3."**

v0.5.0 gave us the floor + audit spine. v0.5.1 gave us the unified gate.
v0.5.2 closes the triptych: when two agents call the gate independently,
arbitration looks across proposers and picks exactly one winner. The
audit spine is the storage layer — proposals ARE `AuditEventKind.INTENT`
events, arbitration is a read-over-audit + decision function. No new
durability layer.

### Arbitration (`sos.kernel.arbitration`)

- **`sos/kernel/arbitration.py`** — three public coroutines:
  - `propose_intent(agent, action, resource, tenant, priority, metadata)` →
    writes an INTENT event tagged `metadata.arbitration=True` with
    `metadata.priority=N`. Returns the proposal id (audit event id).
  - `arbitrate(resource, tenant, window_ms=500, strategy="priority+coherence+recency")` →
    `ArbitrationDecision`. Reads proposals in window, sorts, emits one
    `ARBITRATION` audit event, returns a frozen decision. **Never raises**
    — arbitration failures fall through to a no-winner decision so the
    caller's denial path stays clean.
  - `read_proposals(tenant, resource, window_ms=500)` — observability helper.
- **Rank rule** — `priority → coherence → recency`:
  1. `metadata.priority` (int, higher wins).
  2. Conductance sum `sum(G[agent][skill])` from `sos.kernel.conductance`
     — the agent's proven-flow signal; absent agents score 0.
  3. Later `timestamp` wins.
  Strategy name is recorded in `ArbitrationDecision`, so future strategies
  (`fmaap-weighted`, `governance-tier-aware`) ship as new string values
  without schema churn.
- **`sos/contracts/arbitration.py`** — `ArbitrationDecision` + `LoserRecord`
  frozen Pydantic v2 models. 4 required + 7 optional fields on the decision.
  Additive-only. Baseline locked in `test_arbitration_schema_stable.py` (7 tests).

### Gate integration (opt-in)

- **`sos/kernel/policy/gate.py`** — `can_execute()` grows three kwargs:
  `propose_first: bool = False`, `priority: int = 0`, `window_ms: int = 500`.
  When `propose_first=True`: gate calls `propose_intent` + `arbitrate`;
  winners add `"arbitration"` to `pillars_passed` and flow through normal
  signals; losers short-circuit with a denial whose reason names the winner.
  **Default `propose_first=False` preserves every existing caller unchanged.**

### Legacy shim removed

- **`sos/kernel/governance.py`** — the `~/.sos/governance/intents/{tenant}/`
  file-writing block is gone, per the v0.5.0 CHANGELOG plan. Audit has been
  authoritative for one full minor version. `get_intent_log()` now reads
  from `sos.kernel.audit.read_events` so its return shape is unchanged for
  callers.

### Tests (new, all green)

- **`tests/contracts/test_arbitration_schema_stable.py`** — 7 tests
  (frozen, required/optional fields, no-removal, additive-only).
- **`tests/kernel/test_arbitration.py`** — 7 tests (single-proposer wins,
  higher priority wins, coherence tie-break, recency tie-break, empty
  window no-winner, ARBITRATION event persisted, multi-tenant isolation).
- **`tests/kernel/test_policy_gate_propose.py`** — 3 tests (winner allowed,
  loser denied, `propose_first=False` path unchanged).

### Docs

- **`docs/kernel/arbitration.md`** — public contract doc matching
  `audit.md` / `policy.md` style: why arbitration, architecture insight
  (read-over-audit), surface, rank rule, contracts, gate integration,
  durability, failure modes, end-to-end example.

### Deferred to v0.5.3+

- Per-squad arbitration windows (dynamic, based on coherence).
- Arbitration replay tooling (`sos.cli arbitration replay`).
- Migrating the remaining ~10 service-side permission checks to
  `can_execute` (same mop-up deferred from v0.5.1).

## [0.5.1] - 2026-04-18 — Unified policy gate

**Release theme: "The kernel enforces the blueprint — step 2 of 3."**

v0.5.0 gave us the floor (R0 lock) and the audit spine. v0.5.1 adds the
single gate every HTTP route, every agent action, every kernel-governed
decision asks: *may this agent perform this action on this resource?*
One question, one call, one audited answer. No service reinvents it.

### The gate (`sos.kernel.policy.gate`)

- **`sos/kernel/policy/gate.py`** — new async function
  `can_execute(agent, action, resource, tenant, authorization, capability, context)`.
  Composes five signals in one place:
  1. Bearer verification (`sos.kernel.auth.verify_bearer`)
  2. Tenant scope enforcement (system/admin bypass or exact-match)
  3. Capability (if `SOS_REQUIRE_CAPABILITIES=1`)
  4. FMAAP 5-pillar validation when squad context is present
  5. Governance tier lookup
  Writes exactly one `AuditEventKind.POLICY_DECISION` event per call.
  **Fail-open for availability** (FMAAP DB down → warn + allow),
  **fail-closed for security** (missing/invalid bearer, scope mismatch).
- **`sos/contracts/policy.py`** — `PolicyDecision` frozen Pydantic v2
  model. 5 required + 7 optional fields. Additive-only — new signals
  land as new list members or metadata keys, never schema churn.
  Snapshot baseline locked in `test_policy_schema_stable.py`.
- **`sos/kernel/governance.py::before_action`** now consults the gate
  first. FMAAP pillar failures become authoritative denials without
  governance having to re-implement the checks. Fail-open on gate
  error preserves governance availability.

### Proof-of-concept migration

- **`sos/services/integrations/app.py`** — all 3 authenticated routes
  (`GET /oauth/credentials/{tenant}/{provider}`,
  `POST /oauth/ghl/callback/{tenant}`,
  `POST /oauth/google/callback/{tenant}`) now make one `can_execute()`
  call + `_raise_on_deny()` handoff. The inline `_verify_bearer`,
  `_check_tenant_scope`, and `_require_system_or_admin` helpers are
  gone. Behaviour preserved (same 401/403/200 semantics); every call
  now writes an audit event.

### Tests (new, all green)

- **`tests/contracts/test_policy_schema_stable.py`** — 5 tests snapshotting
  the `PolicyDecision` baseline (frozen, required fields, optional fields,
  no-removal, instance-immutability).
- **`tests/kernel/test_policy_gate.py`** — 7 tests covering system-token
  cross-tenant allow, scoped-token own-tenant allow, scoped-token
  cross-tenant deny, no-scope deny, invalid-bearer deny,
  kernel-internal (no auth) allow, audit event persisted.
- **`tests/services/test_integrations_gate.py`** — FastAPI TestClient
  tests proving the migration preserves 401/403/200/404/400 behaviour.

### Docs

- **`docs/kernel/policy.md`** — public contract doc matching
  `docs/kernel/audit.md` style: what the gate is, what it isn't,
  surface, signals, fail-open/fail-closed, durability, migration guide
  for sibling services.

### Deferred

- **v0.5.1.1+ mop-up:** migrate the ~10 other permission sites across
  `economy`, `registry`, `squad`, `mcp`, `tools` to the gate. Each is
  a ~15-line commit — they do not block v0.5.1.
- **v0.5.2:** `sos/kernel/arbitration.py` conflict-resolution layer
  (needs design work before code). Remove the
  `~/.sos/governance/intents/` legacy compat shim.

## [0.5.0] - 2026-04-18 — Kernel floor lock + unified audit stream

**Release theme: "The kernel enforces the blueprint — step 1 of 3."**

This is the release that makes the kernel provably the floor. Every
`sos.services.*` import has been expunged from `sos/kernel/`, and a
permanent AST sweep test prevents regression. A new unified audit
stream (`sos.kernel.audit`) lands as the canonical sink for every
governed decision; it's designed so v0.5.1 policy and v0.5.2
arbitration can plug in without reopening the kernel.

### R0 floor lock — the last kernel→services leak, closed

- **`sos/kernel/governance.py`** no longer imports
  `sos.services.economy.metabolism`. Budget checks now flow through
  `sos.clients.economy.AsyncEconomyClient.can_spend()` over HTTP.
  Fail-open on any error (connection refused, timeout, 5xx) — economy
  downtime can never block governance.
- **`sos/services/economy/app.py`** gains `GET /budget/can-spend`
  (Bearer-auth, tenant-scoped) that delegates unchanged to
  `metabolism.can_spend(project, cost)`.
- **`sos/clients/economy.py`** gains sync + new `AsyncEconomyClient`
  (subclasses `AsyncBaseHTTPClient`) with matching `can_spend()`
  methods. The async variant is what the kernel uses.
- **Stale R2 ignore removed.** The
  `sos.adapters.telegram → sos.services.economy.metabolism` entry was
  already obsolete (no such import existed) and has been dropped from
  `pyproject.toml`. `lint-imports`: **4 kept, 0 broken.**

### Unified audit stream (`sos.kernel.audit`)

- **`sos/contracts/audit.py`** — new frozen Pydantic model:
  `AuditEvent`, `AuditEventKind`
  (`intent`, `policy_decision`, `action_completed`, `action_failed`,
  `arbitration`), and `AuditDecision` (`allow`, `deny`,
  `require_approval`, `n/a`). Shape designed to accommodate v0.5.1
  policy and v0.5.2 arbitration writers **without schema changes**.
- **`sos/kernel/audit.py`** — three public functions
  (`new_event`, `append_event`, `read_events`) and nothing else. Disk
  write at `~/.sos/audit/{tenant}/{YYYY-MM-DD}.jsonl` is authoritative
  and fsync'd; bus emit to `sos:audit:{tenant}` Redis stream is
  observational and best-effort. Disk never blocks on Redis.
- **`sos/kernel/governance.py`** now writes every intent (and every
  budget denial) through `audit.append_event`. The legacy
  `~/.sos/governance/intents/{tenant}/{date}.jsonl` file is still
  populated as a read-side compat shim — planned removal in v0.5.2.
- **`docs/kernel/audit.md`** — public contract documentation.

### Tests (18 new, all green on first run)

- **`tests/contracts/test_kernel_no_service_imports.py`** — AST sweep
  walking every `sos/kernel/**/*.py` file, asserting **zero**
  `sos.services.*` imports. `_ALLOWED_EXCEPTIONS` is an empty
  `frozenset`; any future addition requires an explicit test edit.
- **`tests/kernel/test_governance_budget.py`** — 3 tests covering
  the three paths of budget consultation: allowed, blocked, fail-open
  on HTTP error.
- **`tests/kernel/test_audit.py`** — 7 tests covering roundtrip
  append+read, filter-by-kind, Pydantic immutability (`frozen=True`),
  disk durability when Redis is down (the whole availability
  guarantee), corrupted-line tolerance, default-today date.
- **`tests/contracts/test_audit_schema_stable.py`** — 5 tests
  snapshotting the v0.5.0 schema baseline. Field removals, rename,
  required-status changes, and missing enum values all fail loudly.
  Additive changes are allowed.

### Durability guarantees

- **Kernel is now provably service-free.** Not by convention; by CI.
- **AuditEvent schema is frozen.** v0.5.1 and v0.5.2 add code, not
  schema churn.
- **Policy gate (`kernel/policy.py`) and arbitration
  (`kernel/arbitration.py`) will land in new files** without
  modifying audit or governance. The kernel reopens to *add* modules,
  not to *modify* them. That is the durability property v0.5.0 buys.

### Deferred (intentional)

- `kernel/policy.py` unified `can_execute()` gate — v0.5.1.
- `kernel/arbitration.py` conflict resolution — v0.5.2 (needs a
  design brainstorm first; "who wins when Loom and Athena disagree"
  is a 488 question, not a coding question).
- `sos/cli` split to drop the three
  `sos.cli → sos.services.*.__main__` dispatcher ignores — pending.
- Removal of the legacy `~/.sos/governance/intents/` shim — v0.5.2.

## [0.4.6] - 2026-04-18 — R2 sweep (clients/adapters/cli/agents)

Closes the P1 micro-kernel violations where non-service code reached into
service internals. Every fix routes through an HTTP client (or inlined env
read). `lint-imports`: **4 kept, 0 broken.** R2 `ignore_imports` shrinks
from 10 entries to 5; the remaining 5 are a documented R2 backlog
(P1-01 MCP gateway, `sos.adapters.telegram` → economy metabolism, and
three `sos.cli` → service `__main__` dispatcher entries — legitimate
packaging pattern pending the v0.5 CLI split).

### P1 closures

- **P1-07 — `clients/operations` → runner over HTTP.** Was importing
  `sos.services.operations.runner` in-process. Now routes through
  `BaseHTTPClient` against the ops runner.
- **P1-02 — `adapters/router` → `clients/economy`.** `ModelRouter` no
  longer imports `sos.services.economy.wallet.SovereignWallet`; it debits
  via `EconomyClient` (HTTP). Concrete LLM adapters (`ClaudeAdapter`,
  `GeminiAdapter`, `OpenAIAdapter`) are now lazy-loaded through a module
  `__getattr__` so optional extras (`gemini`, `openai`) don't fail at
  import time.
- **P1-04 — `agents/shabrang` standalone mining daemon.** Dropped the
  `SOSEngine` base class (never used its chat capability). Now a plain
  `ShabrangAgent` built on `CoherencePhysics` + `MirrorClient` + `Config`.
- **P1-05 — `agents/join` drives journeys via HTTP.** New FastAPI app at
  `sos/services/journeys/app.py` (port 6070) and matching
  `sos.clients.journeys.{JourneysClient, AsyncJourneysClient}`.
  `agents/join` no longer imports `JourneyTracker` or `SYSTEM_TOKEN` from
  squad — admin token resolves from `SOS_ADMIN_TOKEN` /
  `SOS_SYSTEM_TOKEN` env.
- **P1-06 — `cli/onboard` uses `SaasClient`.** Tenant CRUD now flows
  through `sos.clients.saas.SaasClient` +
  `sos.contracts.tenant.TenantCreate` (`model_dump(mode="json")` for
  enum serialization). Drops the two in-process imports of
  `sos.services.saas.registry` and `.models`.

### Tests

- `tests/clients/test_no_service_imports.py` — AST sweep asserting
  no module under `sos/clients/` imports `sos.services.*`.
- `tests/adapters/test_router_economy_client.py` — 6 tests covering
  `_record_cost` debit flow + default wallet factory.
- `tests/agents/shabrang/test_shabrang_decoupled.py` — 3 tests: no engine
  leak, no `SOSEngine` base, constructs without LLM SDKs.
- `tests/clients/test_journeys_client.py` — 7 tests for sync + async
  client endpoint mapping and token resolution.
- `tests/agents/test_join_decoupled.py` — 4 tests for no squad/journeys
  leak + env-driven admin token.
- `tests/cli/test_onboard_decoupled.py` — 4 tests: AST sweep, SaasClient
  import, singleton cache, end-to-end mock of `create_tenant` +
  `activate_tenant`.

### Side fixes

- `SOSLogger` has no `.warning` method — fixed 4 pre-existing
  `log.warning(...)` calls in `sos/adapters/router.py` to use `.warn`.
- `sos/services/journeys/app.py` registers via
  `sos.services.bus.discovery.register_service` on startup (R1 carve-out
  for `services→bus` is the documented P1-10 theme).

## [0.4.7] - 2026-04-18 — MCP R2 sweep (P1-01 closure)

Closes the P1-01 backlog: `sos.mcp.sos_mcp_sse` no longer imports any
`sos.services.*` module. Every cross-namespace reach-in is now replaced
with an HTTP client call. Shipped in four phases so each half-commit is
independently revertable. `lint-imports`: **4 kept, 0 broken.** R2
`ignore_imports` shrinks from 5 to 4 (all four MCP→services entries
dropped; the remaining ignores are `sos.adapters.telegram` → economy
metabolism and three `sos.cli` → service `__main__` dispatcher entries
— out of scope, pending v0.5 CLI split).

### Phase closures

- **Phase 1 — MCP ↔ squad via `SquadClient`.** Dropped in-process
  imports of squad `auth`/`api_keys`. MCP's `/auth/verify` and
  `/api-keys` routes now proxy to the squad service (`:8060`) via
  `sos.clients.squad.SquadClient`. Kernel auth (`sos.kernel.auth`)
  replaces squad's token lookup.
- **Phase 2 — MCP ↔ saas via `SaasClient`.** New endpoints on
  `sos.services.saas.app`: `POST /rate-limit/check`, `POST
  /audit/tool-call`, `GET/POST /marketplace/*`, notification prefs.
  MCP's hot path now awaits `_async_saas_client.check_rate_limit(...)`
  (with fail-open on exception) and fire-and-forgets
  `log_tool_call(...)` via `loop.create_task` so audit never blocks
  the request.
- **Phase 3 — MCP ↔ billing via `AsyncBillingClient`.** New wrapper at
  `sos.services.billing.app` (`:8077`) exposing `/webhook/stripe`.
  MCP proxies the raw Stripe request (bytes + `stripe-signature`
  header) unchanged via `AsyncBillingClient.forward_stripe_webhook`;
  HMAC verification still runs inside the billing handler. MCP no
  longer imports `sos.services.billing.webhook`.
- **Phase 4 — MCP ↔ integrations via `AsyncIntegrationsClient`.** New
  POST endpoints on `sos.services.integrations.app`:
  `/oauth/ghl/callback/{tenant}`, `/oauth/google/callback/{tenant}`.
  MCP's `/oauth/ghl/callback` and `/oauth/google/callback` routes
  now proxy via `AsyncIntegrationsClient`; inline
  `TenantIntegrations` imports removed.

### Client additions

- `sos/clients/billing.py` — new. `BillingClient` (sync, health only)
  + `AsyncBillingClient` with `forward_stripe_webhook(raw_body,
  headers)` that preserves byte-exact payload for HMAC verification.
- `sos/clients/integrations.py` — extends existing client with
  `handle_ghl_callback(tenant, code)` and
  `handle_google_callback(tenant, code, service)` on both sync and
  async variants.
- `sos/clients/saas.py` — extends with `check_rate_limit`,
  `log_tool_call`, `browse_marketplace`, `subscribe_marketplace`,
  `my_subscriptions`, `create_listing`, `my_earnings`, notification
  preferences. 9 new methods × 2 (sync + async).
- `sos/clients/base.py` — `_request()` now accepts `params=` and
  `content=` on both sync and async paths (needed for saas query
  params and billing raw-body forwarding).

### Service additions

- `sos/services/billing/app.py` — new. Minimal FastAPI wrapper around
  the existing `sos.services.billing.webhook.stripe_webhook_handler`.
  Runs on port 8077.
- `sos/services/saas/app.py` — adds rate-limit, audit, marketplace
  (browse/subscribe/my_subscriptions/create_listing/my_earnings), and
  notification preference endpoints. All admin-auth'd.
- `sos/services/integrations/app.py` — adds
  `POST /oauth/{ghl,google}/callback/{tenant}` with
  `_require_system_or_admin` (callbacks require system scope; no
  tenant-scoped caller can complete another tenant's OAuth).

### Tests

- `tests/contracts/test_mcp_no_service_imports.py` — AST sweep on
  `sos/mcp/sos_mcp_sse.py` asserting zero `sos.services.*` imports
  (top-level or inline) with `sos.services.bus.discovery` whitelisted
  as the sole exempt infra shim. Any future MCP→services leak fails
  this test in CI.

## [0.4.8] - 2026-04-18 — Repo hygiene

Pure `git mv` + path updates. Zero behavior change. `lint-imports`:
**4 kept, 0 broken.** Package tree separated from data + docs +
ops utilities before the v0.4.6 / v0.4.7 / v0.5.0 refactors.

### Moves

- **Marketing data out of the package.** `sos/squads/marketing/`
  (Remotion video pipeline, 532MB of assets, skill-graph docs) →
  `data/squads/marketing/`. `sos/squads/trop/` stays — it's still
  a live Python module imported as `sos.squads.trop`. Refs in
  `setup.sh`, `CHANGELOG.md`, `.mkt-lead-claude.md` updated.
- **Top-level docs to `docs/`.** Moved 7 files: `AGENT_TEMPLATE`,
  `ARCHITECTURE`, `PORTING_GEMINI_V1`, `SOVEREIGN_MEMORY`, `TASKS`,
  `TECH-RADAR`, `WHITEPAPER`. Root keeps tool-expected files
  (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CHANGELOG.md`,
  `CONTRIBUTING.md`, `README.md`).
- **Root utilities to `scripts/`.** `create_agent.py`,
  `organ_daemon.py` — doc refs in `organs/README.md` and
  `docs/AGENT_TEMPLATE.md` updated.
- **Agent-seed scripts to `sos/agents/seeds/`.** `bootstrap_river`,
  `onboard_athena`, `onboard_claude_final`, `kasra_onboard`.
- **Dispatcher archive to `.archive/`.** `sos/services/dispatcher.archive/`
  → `.archive/sos-services-dispatcher/`. Keeps `sos/services/`
  siblings list to live services only.
- **Examples dir.** `scripts/demo_ai_to_ai_commerce.py` →
  `examples/demo_ai_to_ai_commerce.py`.

### Hygiene

- **Deprecation banners** on `sos/deprecated/redis_bus.py` and
  `sos/deprecated/sos_mcp.py` (top of file, above module docstring).
  No live imports — R5 stays KEPT.
- **`.gitignore`** adds `graphify-out/`, `graphify-out-*/`,
  `.wrangler/`, `.remember/`, `.code-review-graph/`.

### Acceptance

- `sos/squads/trop` still imports cleanly (`python3 -c 'import sos.squads.trop'`)
- `lint-imports` 4 kept, 0 broken
- Pre-existing failures unrelated to moves (missing `base58` dep,
  schema-count drift test) — none introduced by this sprint

---

## [0.4.5] - 2026-04-18 — P0 decoupling sweep

Closes every P0 R1 violation tracked by `pyproject.toml` import-linter
`ignore_imports`. Services no longer reach across the boundary into
each other's internals; reads go through `sos.clients.*` (HTTP) and
writes go through bus events. `lint-imports`: **4 kept, 0 broken.**

### Waves

- **Wave 1 — `squad → health+journeys`** (P0-04, P0-05)
  squad now emits `task.completed`; `health.bus_consumer` +
  `journeys.bus_consumer` subscribe with the 5-invariant pattern.
- **Wave 2 — `content → engine`** (P0-03)
  `SwarmCouncil` byte-moved to `sos/kernel/council.py`; content calls
  engine through `AsyncEngineClient` (`ChatRequest`).
- **Wave 3 — `analytics → integrations`** (P0-06)
  New `sos/services/integrations/` FastAPI service on port 6066.
  `sos/clients/integrations.py` — sync + async client with tenant-scoped
  bearer auth. `analytics/ingest.py` converted to async.
- **Wave 4 — `autonomy → identity`** (P0-07)
  `UV16D` moved to `sos/contracts/identity.py`.
  `sos/clients/identity.py` — `generate_avatar`, `on_alpha_drift`.
  identity service gains `POST /avatar/generate` + social endpoints.
- **Wave 5 — `brain → registry`** (P0-09)
  New `sos/services/registry/` FastAPI service on port 6067.
  `sos/clients/registry.py` returns `AgentIdentity` (reconstructed from
  HTTP payloads). brain no longer imports the registry module.
- **Wave 6 — `dashboard → economy + registry`** (P0-12)
  `EconomyClient.list_usage` deserializes into typed `UsageEvent`.
  dashboard `tenants.py` + `routes/sos_operator.py` routed through
  `EconomyClient` / `RegistryClient`.
- **Wave 7 — `engine → tools`** (P0-02)
  Engine held a redundant in-proc `ToolsCore` alongside `ToolsClient`.
  Removed; chat tool-exec loop routes through the HTTP client.
- **Wave 8 — conductance → kernel** (P0-10, P0-11)
  `conductance_*` helpers + `CONDUCTANCE_FILE/ALPHA/GAMMA` moved to
  `sos/kernel/conductance.py`. `feedback.loop` + `journeys.tracker`
  import from kernel; `health.calcifer` re-exports for BC.
- **Wave 9 — `billing → saas`** (P0-01)
  `sos/clients/saas.py` — sync + async `SaasClient` (admin-key
  bearer). `billing.webhook` calls `create_tenant` /
  `activate_tenant` / `cancel_tenant` over HTTP. `TenantCreate` /
  `TenantUpdate` remain as the type contracts; `.model_dump()` at
  the HTTP boundary.

### Added

- `sos/kernel/council.py` — `SwarmCouncil` shared between engine + content.
- `sos/kernel/conductance.py` — conductance matrix, shared kernel primitive.
- `sos/contracts/identity.py` — `UV16D` as a pure dataclass.
- `sos/services/integrations/` — oauth-credentials HTTP service.
- `sos/services/registry/` — agent-roster HTTP service.
- `sos/clients/integrations.py`, `sos/clients/identity.py`,
  `sos/clients/registry.py`, `sos/clients/saas.py` — new HTTP clients
  using the same `BaseHTTPClient` / `AsyncBaseHTTPClient` pattern.
- Bus consumers: `health.bus_consumer`, `journeys.bus_consumer` — five-
  invariant Redis consumer pattern.

### Removed

- `sos/services/engine/council.py` — replaced by `kernel/council.py`.

### Structural enforcement

R1 ignore list shrunk from 15 entries (all P0 + P1-10) to just the
P1-10 `bus.discovery` carve-outs pending kernel move. Every sprint
from here is P1 cleanup or v0.5 kernel consolidation.

Refs: `docs/plans/2026-04-17-sos-sprint-roadmap.md`

## [0.4.4] - 2026-04-17 — Structural foundation

### Added
- `sos/kernel/bus.py`, `sos/kernel/auth.py`, `sos/kernel/health.py`,
  `sos/kernel/config.py`, `sos/kernel/policy/` — shared infrastructure
  extracted from `services/` into kernel/ where it belongs.
- `sos/contracts/tenant.py` + `sos/contracts/schemas/tenant_v1.json` —
  first-class Tenant contract (was scattered across saas, billing, cli).
- `sos/contracts/errors.py` — `BusValidationError`,
  `MessageValidationError` live here; `tests/contracts/` stops importing
  service internals (R6).
- `UsageEvent` moved to `sos/contracts/economy.py`.
- `docs/sos-method.md` — one-page method, machine-enforced.
- `import-linter` contracts in `pyproject.toml` enforcing R1/R2/R5/R6.
- `.pre-commit-config.yaml` runs `lint-imports` + `tests/contracts/` on
  every commit.

### Removed
- `sos/services/bus/core.py`, `sos/services/auth/`,
  `sos/services/_health.py`, `sos/services/common/` — all relocated to
  `kernel/` or `kernel/policy/`.
- `sos/services/saas/models.py` — `Tenant*` moved to `contracts/`.

### Refactored
- FMAAP (`kernel/policy/fmaap.py`) no longer imports `squad.service`
  internals; reads `DB_PATH` from `kernel/config.py`.
- `tests/contracts/test_messages_unified.py` moved to
  `tests/services/bus/test_messages_unified.py` (imports
  `bus.enforcement`, which is a service — violated R6).
- `tests/contracts/test_errors.py` — 4 enforcement-backcompat tests
  relocated to `tests/services/bus/test_enforcement_errors.py` for the
  same reason.

### Structural enforcement
- `lint-imports` passes all four contracts at v0.4.4 tag. Known v0.4.5+
  P0s listed in `[tool.importlinter.contracts].ignore_imports` — each
  sprint shrinks that list.

Refs: `docs/plans/2026-04-17-sos-structural-audit.md`,
      `docs/plans/2026-04-17-sos-sprint-roadmap.md`

## [0.4.3] - 2026-04-17 — "Dispatcher + Brain + Code Mode"

Plan: [`docs/plans/2026-04-17-v0.4.3-squad-sprint.md`](docs/plans/2026-04-17-v0.4.3-squad-sprint.md)

### Added
- **Brain service (`sos/services/brain/`)** — bus consumer that obeys the 5 invariants (idempotency, per-stream checkpoints, fail-open, SCAN discovery, replay tolerance). Now scores every `task.created`, emits `task.scored`, maintains a priority queue, dispatches to the best-matching agent via `ProviderMatrix`, and emits `task.routed`. Priority queue is FIFO-stable via `(−score, counter, task_id)` heap.
- **Scoring** — `score_task(impact, urgency, unblock_count, cost)` formula in `sos/services/brain/scoring.py`. URGENCY_WEIGHTS: critical=4.0, high=2.0, medium=1.0, low=0.5. Defaults when payload is thin: impact=5, urgency=payload.priority or "medium", unblock=0, cost=1.0.
- **Matrix** — `sos/services/brain/matrix.py::select_agent` — picks largest skill overlap, breaks ties by lowest agent load then lex name. Returns `None` on zero overlap.
- **`task.scored` contract** — JSON Schema (`sos/contracts/schemas/messages/task.scored_v1.json`) + Pydantic binding (`TaskScoredMessage` / `TaskScoredPayload` in `sos/contracts/messages.py`) + enforcement registration in `sos/services/bus/enforcement.py`.
- **BrainSnapshot contract** — HTTP response shape for the dashboard (not a bus type). Schema at `sos/contracts/schemas/brain_snapshot_v1.json`, Pydantic at `sos/contracts/brain_snapshot.py`. Fields: `queue_size`, `in_flight`, `recent_routes` (≤50), `events_by_type`, `events_seen`, `last_update_ts`, `service_started_at`.
- **`GET /sos/brain`** — operator dashboard endpoint (`sos/services/dashboard/routes/brain.py`). Reads the Brain's snapshot from Redis key `sos:state:brain:snapshot` (TTL 30s, written at end of every tick) — avoids cross-process imports. Bearer-protected, `503` on cache miss.
- **`GET /sos/pairing/nonce` + `POST /sos/pairing`** — agent pairing endpoints (`sos/services/saas/pairing.py`). ed25519 pubkey + nonce challenge + signed response → bearer token registered (hash-only) in `tokens.json` under `scope=agent`. Agent ID deterministic: `<Name>_sos_NNN`. Signature verified via `cryptography.hazmat.primitives.asymmetric.ed25519`.
- **Pairing contract** — schema at `sos/contracts/schemas/pairing_v1.json` (`$defs.PairingRequest` + `$defs.PairingResponse`) + Pydantic at `sos/contracts/pairing.py`.
- **`sos/cli/pair-agent.sh`** — one-shot agent provisioning script. Generates ed25519 keypair, fetches nonce, signs, pairs, writes token to `~/.sos/token` (0600), smoke-tests host. Turns "onboard a fresh agent" into one command.
- **Code Mode MCP** — `sos/mcp/code_mode.py`. Tool calls become Python snippets executed in a restricted `exec` sandbox; only the final value returns to the model. Stdout/stderr captured via `contextlib.redirect_stdout/stderr`; timeout via `asyncio.wait_for(asyncio.to_thread(...))`; trailing expression auto-rewritten to `_last = <expr>`. Targets Cloudflare's ~99.9% token reduction on tool-heavy flows. Wired into `sos_mcp_sse` gateway as the `code_mode` tool, exposing a narrow safe-tool namespace (`status, peers, memories, recall, search_code, task_board, task_list`).
- **OpenAPI** — `sos/contracts/openapi/dashboard.yaml` (adds `/sos/brain` + BrainSnapshot/RoutingDecision) and `sos/contracts/openapi/saas.yaml` (adds `/sos/pairing/*` + PairingRequest/PairingResponse). Both validated with `openapi-spec-validator`.
- **`sos-brain-wire` specialist** — new Sonnet stateless subagent (`.claude/agents/sos-brain-wire.md`). One-shot Brain deliverable: touch ≤2 files, write one unit test, must obey the 5-invariant bus-consumer pattern. Joins the reused v0.4.0 squad (schema-author, pydantic-author, openapi-author, contract-tester, connectivity-medic).

### Changed
- **`BrainService` emits cross-service state via Redis, not Python imports.** At end of every `_tick()`, writes `sos:state:brain:snapshot` (30s TTL). Dashboard reads from this key instead of importing BrainService — clean inter-process boundary.
- **`sos/services/brain/state.py`** gained `priority_queue`, `_queue_counter`, `enqueue`, `pop_highest`, `queue_size`, `task_skills`.
- **`agent_joined` handling** — the payload lacks skills/capabilities, so dispatch looks them up via `sos.services.registry.read_all()` called through `asyncio.to_thread` from the async consumer.

### Fixed
- **Heap FIFO stability** — tie-breaking `(−score, counter, task_id)` ensures equal-score tasks pop in insertion order, not arbitrary string order.

### Architectural invariants
- Brain obeys the 5 bus-consumer invariants. Every new handler must too — violations are blockers.
- Cross-service state hand-off goes through Redis keys with TTL, never through in-process imports. The `sos:state:<service>:snapshot` convention is established.
- Pairing tokens are stored **hash-only** in `tokens.json`; plaintext is returned to the caller exactly once and never persisted.
- Schema catalog is the source of truth — `enforcement.py::_V1_TYPES` mirrors `messages.py::MessageType` mirrors `schemas/messages/<type>_v1.json` filenames.

### Tests
- 103 new green tests across brain / contracts / services / mcp: priority queue (6), scoring integration (4), matrix (7), dispatch (5), task.scored contract (14), brain_snapshot contract (14), pairing contract (20), dashboard brain route (5), pairing endpoint (9), code_mode unit (12), code_mode integration (6), brain E2E (1 — full scoring → dispatch → dashboard round-trip against fakeredis).

### Commits
- Single squad sprint: 28 steps across 6 sprints, 5 reused specialists + 1 new (`sos-brain-wire`), parallel dispatch where independent.

## [0.4.1] - 2026-04-18 — "Moat + Coherence"

### Added
- **Skill Registry with provenance** — `SkillCard v1` (JSON Schema Draft 2020-12 + Pydantic + 67 contract tests). Provenance fields: `author_agent`, `authored_by_ai`, `lineage[]`, `earnings{}`, `verification{}`, `commerce{}`, `runtime{}`. Mumega's moat primitive. Competes with ClawHub / Vercel skills.sh on **provenance, not volume**.
- **Public skill marketplace** at `https://app.mumega.com/marketplace` — card grid of every `marketplace_listed: true` skill with earnings proof line, verification badge, price, author. Detail pages at `/marketplace/skill/{id}` (incl. `?format=json`). Unauthenticated.
- **Operator dashboard** at `https://app.mumega.com/sos` — Phase 0 (flow map) + Phase 1 (Overview + Agents) + Phase 2 (Money pulse + Skill moat). Admin-scoped. Now renders a 30-node / 38-edge service map with license-split badges (Community / Proprietary / External).
- **Customer dashboard** tenant moat panels — "Your Skills", "Your Earnings", "Recent Usage" on `/dashboard`.
- **Agent OS product page** at `mumega.com/products/agent-os` — pricing, install one-liner, marketplace link, pitch.
- **SOS lab page** at `mumega.com/labs/sos` — engineering surface (contracts, changelog, architecture).
- **`mumega.com/install`** shell script — 30-second Mac onboarding. Signup + `.mcp.json` + `~/.claude.json` patching, per-tool snippets for Cursor / Codex / Gemini CLI / Windsurf.
- **Internal SkillCards** (8 so far) — Mumega **dogfoods its own skill registry** for its own cleanup work. Every maintenance commit is driven by a SkillCard invocation with provenance + earnings tracking.
- **Economy UsageLog** (`POST /usage`, trop issue #98) — currency-agnostic (`cost_micros`), tenant-scoped, append-only JSONL. Enables edge tenants (CF Workers) to ingest model-call telemetry.
- **AI-to-AI commerce demo** — `scripts/demo_ai_to_ai_commerce.py`. Cross-squad purchase with $MIND settlement, full UsageLog trace, earnings bump on author skill.
- **Provider Matrix simplified** — `sos/providers/matrix.py` with `ProviderCard`, `CircuitBreakerConfig`, `CircuitBreaker` 3-state machine (closed / open / half_open), `load_matrix()`, `select_provider()`. YAML config at `sos/providers/providers.yaml`. Health-probe CLI scaffold at `sos/providers/health_probe.py`. **25 new contract tests** on ProviderCard JSON Schema (v0.4.1 same-day add).
- **SquadTask v1** — schema + Pydantic binding + 57 tests. Squad Service has a typed contract for the first time. Sole source of truth for task state.
- **UsageEvent v1** — JSON Schema Draft 2020-12 matching existing Pydantic + 25 roundtrip tests. Completes the contracts set for v0.4.1.

### Changed
- **Mirror subscribes to the bus.** New `mirror_bus_consumer.service` (systemd --user) tails `sos:stream:global:*`, auto-writes engrams with embeddings on every v1 send / task_completed / announce. Retires 5+ synchronous `mirror_post("/store", ...)` call sites from `sos_mcp_sse.py`. **Kills 3 debt items** (Mirror-no-bus-consumption, UsageLog-not-mirrored, write-amplification).
- **Squad Service is the single source of truth for tasks.** Mirror `/tasks` endpoints retired → HTTP 410 Gone. SOS `sos/mcp/tasks.py` callers repointed to `http://localhost:8060`. **Ends the double task system.**
- **Single auth module.** `sos/services/auth/__init__.py` ships `verify_bearer(authorization) -> AuthContext | None`. 4 call sites migrated (dashboard `_verify_token`, economy `_verify_bearer`, MCP SSE token path, Mirror `resolve_token`). Env-var fallback (`SOS_SYSTEM_TOKEN`, `MIRROR_TOKEN`) preserved. 30-second TTL + mtime cache.
- **Dispatcher scope-trim.** `sos/services/dispatcher/` + `workers/sos-dispatcher/` archived (`.archive/` suffix, `ARCHIVED.md` explains retirement). Post-competitive-scan pivot: runtime is commoditized by OpenAI/Anthropic/Google/MS; Mumega's moat is economy + provenance + coordination.
- **Deprecated code consolidated.** `sos/mcp/redis_bus.py` + `sos/mcp/sos_mcp.py` stdio moved to `sos/deprecated/` via git mv. `remote.js` kept — actively served as `/sdk/remote.js` by bridge.

### Fixed
- **Trop #97** — adapter pricing tables stale. Refreshed Gemini / Anthropic / OpenAI catalogs with 2026-04-17 data. Added `PricingEntry` with flat-per-call support (Imagen 4 / DALL-E 3). Source-linked every non-zero entry.
- **Trop #98** — edge tenants had no way to report usage to the platform ledger. Shipped `POST /usage` + UsageLog.

### Architectural invariants
- Contract coverage extended: **Agent Card v1 + Messages v1 (8 types) + SkillCard v1 + UsageEvent v1 + ProviderCard v1 + SquadTask v1**.
- Bus strict enforcement: unknown message types reject with `SOS-4004`.
- Every non-zero pricing entry carries a `source:` audit tag.
- `app.mumega.com/sos` is the single operator glass: flow map, agents, bus pulse, money pulse, skill moat, incidents.

### Shipped sprint pattern
- **Dogfood loop:** every cleanup commit references a SkillCard; each SkillCard's `earnings.invocations_by_tenant.mumega` increments by 1. Pattern proves the platform maintains itself with its own primitives — strongest possible moat proof.

### Tests
- 403 passed at tag (was 185 at v0.4.0). Contract suite alone: 230 tests (was 46 at v0.4.0).

### Commits
- 18 commits from `v0.4.0` through `v0.4.1` on branch `codex/sos-runtime-validation`.

## [0.1.0] - 2026-02-03

### Added
- **CLI**: `mumega` command with doctor, chat, start, status, version
- **Engine**: Multi-model support (Gemini, Claude, GPT, Grok, Ollama)
- **Resilience**: Circuit breakers, rate limiting, failover router
- **Autonomy**: Dream synthesis, pulse scheduling, coordinator
- **Memory**: Tiered storage with Cloudflare backends
- **Identity**: QNFT system, capability-based access control
- **Errors**: Protocol-level error codes (1xxx-8xxx ranges)
- **Config**: Validation system with `.env.example`
- **Tests**: Unit tests for resilience, CLI, autonomy, dreams
- **Security**: Hardened docker-compose, secret scanning
- **Docs**: OpenClaw learnings, plugin model

### Infrastructure
- PyPI package name: `mumega`
- Python 3.10+ required
- Optional dependencies: gemini, openai, local, full

## [0.1.1] - 2026-02-03

### Added
- **Security**: SSRF protection for external API calls (#47)
- **Security**: Scope-based authorization system (#50)
- **Security**: Ed25519 capability signature verification (#1)
- **Observability**: Prometheus metrics for circuit breakers, rate limiters, dreams, autonomy (#18)
- **Reliability**: Gateway failover with circuit breaker persistence (#15)
- **Testing**: Load tests for rate limiter and circuit breaker (#20)
- **Ops**: Prometheus alerting rules for SOS services (#23)

## [0.4.0] - 2026-04-17

### Added
- **All legacy producers migrated to v1 types**:
  - `sos/bus/bridge.py` `sos_msg()` — builds via `SendMessage`/`AnnounceMessage` Pydantic models. Legacy `msg_type="chat"` is mapped to `"send"`; legacy target `"broadcast"` is normalized to `"sos:channel:global"`.
  - `sos/mcp/sos_mcp.py` `sos_msg()` — same migration. The deprecated MCP stdio entry-point emits v1 on the wire.
  - `sos/mcp/sos_mcp_sse.py` broadcast handler — uses `SendMessage` directly with channel target (parallel to the send handler shipped in beta.1).
  - `sos/mcp/redis_bus.py` `sos_message()` — same v1 mapping, kept for backwards parameter compatibility with older MCP installs.
- **Strict enforcement** at `sos/services/bus/enforcement.py`. `enforce()` now rejects unknown types with `SOS-4004` (legacy-tolerance window closed). `enforce_or_log()` preserved for gradual rollout call sites that may still need it.

### Changed
- The legacy `{"type": "chat", ...}` and `{"type": "broadcast", ...}` shapes no longer exist anywhere in the SOS codebase as a producer. Consumers that read legacy entries from historical Redis streams continue to work because `from_redis_fields()` on `BusMessage` is tolerant of both shapes.
- Contract tests: 56/56 passing (8 schema files × canonical target pattern × round-trip symmetry).

### Sprint delivery notes
- Sprint 3 was a ~30-minute integration pass (no subagent dispatch; each migration is 30–50 lines and reads best done in-process).
- Running services picked up the migrations on next restart. Existing long-lived bus messages in historical streams (pre-0.4.0 legacy shape) are read unchanged by `from_redis_fields()`.

## [0.4.0-beta.1] - 2026-04-17

### Added
- **Contracts: v1 "send" type in production** — primary MCP SSE gateway send handler now builds via `SendMessage` Pydantic model. Source, target, timestamp, message_id validated on construction. Legacy "chat" type retired from this call path.
- **Structured payloads** on the bus: `payload: {"text": "...", "content_type": "text/plain"}` (was: JSON-stringified single-level object).

### Changed
- `sos/mcp/sos_mcp_sse.py` send handler produces v1 messages; inbox + wake-daemon continue to parse identically (payload is still JSON-encoded in Redis hash field).

### Known remaining (closed in 0.4.0)
- `sos/bus/bridge.py`, `sos/mcp/redis_bus.py`, `sos/mcp/sos_mcp.py` still produce legacy "chat"/"broadcast" types. They flow through `enforcement.enforce()` legacy-tolerant path. Migration scheduled for Sprint 3+.

## [0.4.0-alpha.2] - 2026-04-17

### Added
- **Message schema registry v1** — 8 JSON Schema files under `sos/contracts/schemas/messages/` (announce, send, wake, ask, task_created, task_claimed, task_completed, agent_joined). Draft 2020-12. All 8 share a normalized target pattern supporting multi-level channels (`sos:channel:private:agent:athena` etc.).
- **Pydantic bindings** at `sos/contracts/messages.py`. `BusMessage` base + 8 typed subclasses with nested `*Payload` models. `parse_message()` dispatcher. `to_redis_fields()` + `from_redis_fields()` symmetric round-trip.
- **Enforcement module** at `sos/services/bus/enforcement.py`. `enforce()` and `enforce_or_log()`. Legacy-tolerant (known v1 types validated strictly; unknown types pass through for migration window). Error codes SOS-4001/4002/4003.
- **46 new contract tests** across `tests/contracts/test_messages_*.py`. 56 total passing (Agent Card + Messages).
- **Claude.ai sos-claude connector identity fixed** — `tokens.json` entry "Claude.ai — Hadi browser agent" now maps to `agent: hadi` (was incorrectly `agent: kasra`, self-contradicting with its own label). Flat identity resolved for Hadi's primary claude.ai surface.

### Changed
- Nothing under `sos/kernel/` or public service interfaces changed. Contracts layer is additive.

### Architectural
- Sprint 1 delivery pattern validated: 12 Sonnet subagent dispatches in parallel/sequential, 1 Opus architectural gate review (Athena found 4 real drift issues, all fixed), ~45min wall time, ~$4.50 total subagent spend.

## [0.3.0] - undated

Bulk of v0.3.x work predates this changelog's consistent maintenance. See
git log between tags `v0.2.0-beta2` and `v0.4.0-alpha.2` for the chronology,
including: SaaS service + Stripe + Resend, Inkwell v4/v5, mumega-edge worker,
multi-seat tokens, build queue, audit logging, rate limiting, RBAC,
notification router + webhooks, MSG-002/003, SEC-001/002/004/005, PIP-001/002/003,
customer tool gating, signup → build pipeline, ToRivers marketplace.

## [Unreleased]

### Pending
- **Service restart** to pick up the 0.4.0 producers (sos_mcp_sse, sos_mcp, bridge, redis_bus). Running processes import `sos_msg()` at boot and continue to use the legacy builder until restart.
- Per-agent token migration for mumega-hosted tmux agents (kasra deferred per +500k context decision). Only sos-medic + trop provisioned with per-agent tokens so far.
- v0.4.1 Provider Matrix (deterministic LLM routing, circuit breakers, FMAAP Metabolism extension).
- v0.4.2 External observability plane (mumega-watch on a second VPS, not Cloudflare).
- Additional model provider integrations.
