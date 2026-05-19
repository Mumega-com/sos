# Phase 3 — Mesh Enrollment (v0.9.2)

**Status:** planned 2026-04-19
**Target:** v0.9.2
**Blocks:** task #208 (EPIC), task #33 (Hadi action)
**Reference:** `docs/plans/2026-04-19-mumega-mothership.md` Phase 3 (steps 3.1–3.7)

## Problem

Agents today self-announce via Squad Service `/agents/register` and the Redis overlay at `sos:cards:<project>:<name>` (TTL 300s). There is no single authoritative mesh — squad subjects like `squad:growth-intel.{project}` do not resolve at delivery time, and stale agents linger until their TTL lapses silently. The mothership plan calls for a `/mesh/enroll` endpoint, squad addressability, and heartbeat-driven pruning (5m stale / 15m remove) so Phase 4's Mumega-edge can route by squad subject with confidence.

## Chosen approach — Option B (extend `/agents/cards`)

Selected on **durability, trust, security, intelligence**:

- **Durability** — reuses the mature `write_card` path with its Redis TTL; no new keyspace to maintain across restarts.
- **Trust** — AgentCard stays the single source of truth. No sync-drift failure mode between two stores of squad membership.
- **Security** — inherits `_verify_bearer` + `_resolve_project_scope` on the existing `/agents/cards` POST; no new auth surface to harden.
- **Intelligence** — mycelium principle (extend don't duplicate). Saved in memory from a prior incident.

Rejected alternatives:

- **Option A (new `sos:mesh:` keyspace)** — third keyspace, two heartbeat paths that drift, new endpoint for agents to learn on top of `/agents/register`.
- **Option C (hybrid with squad-members index)** — O(1) squad lookup at scale, but the AgentCard + index dual-source-of-truth is the classic sync-bug failure mode. Revisit if mesh grows past ~500 agents.

## Squad composition

One squad across the full sprint — no rotation, no new specialists introduced mid-flight.

| Role | Model | Waves |
|---|---|---|
| Integrator | Opus (this session) | W7, dispatch, conflict resolution |
| SOS Medic | Stateful agent | All waves — triage + failure-mode memory |
| Sonnet A | Sonnet (no-context) | W0 + W1 — contract + endpoint |
| Sonnet B | Sonnet (no-context) | W2 + W3 — resolver + pruner |
| Sonnet C | Sonnet (no-context) | W4 + W5 — agent integration + dashboard |
| Haiku | Haiku (no-context) | W6 — contract test only |

### How no-context subagents work

Each Sonnet/Haiku brief is self-contained:

1. Goal statement (one sentence).
2. Exact files to touch (absolute paths).
3. Acceptance test (pytest command + expected output).
4. Guardrails: R1 import-linter contract, OTEL trace-id preservation if touching a consumer, `black --target-version py311`, ruff `E,F,W`.
5. Commit message format: `feat(<area>): <verb> <what>` with Phase 3 wave tag.
6. Scope ceiling: if the subagent needs to touch a file outside its brief, it must stop and report back rather than widen scope.

Subagents do **not** message each other. Opus is the bus. Every handoff returns to the main session.

### How SOS Medic stays useful

SOS Medic is the only stateful agent in the squad. Between waves it:

- Tails `pytest tests/contracts/` + `pytest -m integration` on a loop.
- Holds a rolling log of failure signatures across the sprint (e.g. "import-linter R1 flared on squad.resolve_subject in W2 — kernel should not grow a resolver").
- On a red, it diagnoses first and reports to Opus; it does not attempt the fix itself (Sonnet/Haiku own the implementation surface).
- Carries the memory of decisions Opus made mid-wave so a later wave doesn't undo them.

## Waves

Each wave = one independently revertable commit with green hooks + tests. Same discipline as Phase 2.

### W0 — AgentCard field extension  (Sonnet A)

- **File:** `sos/contracts/agent_card.py`
- **Change:** add optional `heartbeat_url: str | None = None` field. Round-trip in `to_redis_hash()` / `from_redis_hash()` — empty string ↔ `None`.
- **Test:** `pytest tests/contracts/test_agent_card.py` — new case asserts round-trip parity with and without the field.
- **Commit:** `feat(contracts): add heartbeat_url to AgentCard (phase3/W0)`

### W1 — `POST /mesh/enroll` endpoint  (Sonnet A)

- **File:** `sos/services/registry/app.py`
- **Change:** new route. Body: `{agent_id: str, name: str, role: str, skills: list[str], squads: list[str] = [], heartbeat_url: str | None = None}`. Builds AgentCard via `AgentCard(...)`; calls `write_card(redis, card, ttl_seconds=300)`. Auth via existing `_verify_bearer`; project scope via existing `_resolve_project_scope`. Returns `{enrolled: true, expires_in: 300}`.
- **Test:** new `tests/services/registry/test_mesh_enroll.py` — with system token, POST creates `sos:cards:<project>:<name>`; scoped token enrolls into own project; foreign scope 403.
- **Commit:** `feat(registry): POST /mesh/enroll on top of AgentCard (phase3/W1)`

### W2 — `GET /mesh/squad/{slug}` resolver  (Sonnet B)

- **File:** `sos/services/registry/app.py`
- **Change:** new route. Reads all cards in scope via `read_all_cards(redis, project=...)`; filters by `slug in card.squads`; returns `{slug, agents: [card.to_public_dict() ...], count}`.
- **Test:** new `tests/services/registry/test_mesh_squad_resolve.py` — seed 3 cards, 2 with `"growth-intel"` in `squads`; GET returns 2.
- **Commit:** `feat(registry): GET /mesh/squad/{slug} resolver (phase3/W2)`

### W3 — Heartbeat pruner  (Sonnet B)

- **File:** `sos/services/registry/pruner.py` (new); wired via FastAPI startup hook in `app.py`.
- **Change:** async task every 60s. Scan `sos:cards:*`; compute `age = now - card.last_seen_ts`. If `age > 300`: set `card.stale = True`, `write_card(ttl=remaining)`. If `age > 900`: `DEL` the key. Use `redis.scan_iter` with `match="sos:cards:*"`, `count=200` to avoid blocking.
- **Test:** new `tests/services/registry/test_pruner.py` — fake clock advances 301s → stale flag set; advance to 901s → key deleted.
- **Commit:** `feat(registry): heartbeat-driven stale + remove pruner (phase3/W3)`

### W4 — Agent bootloader calls `/mesh/enroll`  (Sonnet C)

- **Files:**
  - `sos/clients/registry.py` (or wherever `RegistryClient` lives) — add `async def enroll_mesh(agent_id, name, role, skills, squads, heartbeat_url)`.
  - `sos/agents/join.py::AgentJoinService.join()` — after step 8 (bus announce), insert step 8.5: `await registry_client.enroll_mesh(...)`.
- **Test:** extend existing `tests/test_agent_join.py` — assert `sos:cards:<project>:<agent>` exists after `join()` returns.
- **Commit:** `feat(agents): join() enrolls into mesh via /mesh/enroll (phase3/W4)`

### W5 — Dashboard Mesh tab  (Sonnet C)

- **Files:**
  - `sos/services/dashboard/routes/mesh.py` (new) — `GET /brain/mesh` returns JSON of all cards + squad groupings.
  - Matching React component under the dashboard UI tree.
- **Change:** JSON endpoint reuses `RegistryClient.list_cards` (already being added by Phase 2's deferred follow-up — if not yet, temporary direct read with the same import-linter ignore already in `pyproject.toml:270`).
- **Test:** `tests/services/dashboard/test_mesh_route.py` — response schema matches `{agents: [...], squads: {slug: [...]}, last_pruned_at: ...}`.
- **Commit:** `feat(dashboard): mesh tab shows live agents + squads + heartbeat age (phase3/W5)`

### W6 — Contract test  (Haiku)

- **File:** `tests/contracts/test_mesh_enroll_in_bootloader.py` (new)
- **Change:** AST scan of every `sos/agents/*.py` (and `sos/agents/*/boot.py`) that defines a top-level async `join`, `boot`, or `run` function. Assert the function body (transitively, via called helpers within the same file) references `enroll_mesh` or the literal string `/mesh/enroll`. Whitelist non-agent utilities by name.
- **Commit:** `test(contracts): every agent bootloader calls /mesh/enroll (phase3/W6)`

### W7 — Release v0.9.2  (Opus)

- **Files:** `pyproject.toml` (version 0.9.1 → 0.9.2), `CHANGELOG.md` (new `[0.9.2] — 2026-04-<DD>` section), git tag `v0.9.2`.
- **Check:** all contract tests + bus integration tests green before tag.
- **Push:** tag to `origin`.
- **Close:** tasks #33, #208.
- **Commit:** `chore(release): v0.9.2 — mesh enrollment + squad addressability (phase3/W7)`

## Acceptance criteria

- [ ] `POST /mesh/enroll` writes an AgentCard with 300s TTL and respects project scope.
- [ ] `GET /mesh/squad/{slug}` resolves squad subjects at delivery time.
- [ ] Pruner marks stale at 5m, deletes at 15m.
- [ ] Every agent in `sos/agents/*` calls `/mesh/enroll` during boot (enforced by contract test).
- [ ] Dashboard `/brain/mesh` shows live agents, squads, heartbeat age, stale flag.
- [ ] `v0.9.2` tagged and pushed.
- [ ] import-linter R1 contract still green (no new services-to-services imports).
- [ ] Bus integration tests still green (Phase 2 regression check).

## Out of scope

- **No new keyspace** — AgentCard is the only record of an enrolled agent.
- **No squad-members index** — squad resolution is a scan over scoped cards. Revisit in Phase 4+ if scan latency matters.
- **No changes to Squad Service `/agents/register`** — that path stays; `/mesh/enroll` is the registry-side overlay.
- **No heartbeat-URL-pinging** — we accept `heartbeat_url` on the card but do not actively poll it in v0.9.2. Polling lives in Phase 4 with Mumega-edge.
- **No migration of existing cards** — live cards picked up on next boot when agents re-enroll.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| R1 flare — pruner or resolver accidentally reaches across services | SOS Medic tails `lint-imports` on every commit in the sprint |
| Pruner delete races with a concurrent `/mesh/enroll` for the same agent | `write_card` is a HSET + EXPIRE; pruner DEL after write is eventually consistent and next enroll re-creates. Acceptable. |
| AST contract test (W6) false-positives on helper modules in `sos/agents/` | Explicit whitelist by filename in the test; failure message names the file so a human can decide |
| Dashboard tab depends on `RegistryClient.list_cards` that Phase 2 deferred | W5 uses the existing `pyproject.toml:270` import-linter ignore as fallback; swap to client when it lands |

## Progress log

- 2026-04-19 — plan authored. Waves not yet started. Depends on Phase 2 v0.9.1 (shipped).
