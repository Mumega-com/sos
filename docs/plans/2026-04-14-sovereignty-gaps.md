# Plan: Close the Three Sovereignty Gaps

**Date:** 2026-04-14
**Decision:** Stay sovereign — adopt zero frameworks, close gaps ourselves
**Consensus:** Kasra (Claude), Codex (GPT-5.4), Gemini (Gemini 3) — unanimous
**GitHub:** Mumega-com/sos#94

## Architecture Constraint

SOS is a microkernel. All changes must respect:

```
Kernel (sos/kernel/) — schema, identity, capability, registry, config, agent_registry
    ↑ services import from kernel, NEVER the reverse
Services (sos/services/) — bus, engine, squad, memory, health, economy
    ↑ communicate via bus (Redis) or HTTP clients, NEVER direct imports
Contracts (sos/contracts/) — abstract interfaces each service implements
Agents — connect via MCP SSE (:6070), use standard Message format
```

**Rules:**
- Kernel never imports services
- Service-to-service communication is ALWAYS through the bus
- New state goes in kernel (enums, types) or service (implementation)
- Dynamic discovery via `ServiceRegistry` — no hardcoded ports

## Status

### Gap 1: Explicit Lifecycle State Machine — DONE

**Owner:** Codex
**Files changed:**
- `sos/kernel/agent_registry.py` — Added `WarmPolicy` enum (WARM/COLD), `get_warm_agents()`, `get_cold_agents()`
- `sos/services/health/lifecycle.py` — STUCK_MINUTES 30→120, parked-state detection, cold agent handling
- `tests/test_lifecycle_contract.py` — 7 tests passing

**Architecture compliance:**
- New enum (`WarmPolicy`) correctly placed in kernel (it's agent metadata, not service logic)
- Parked state persisted to `~/.sos/state/{agent}.json` (service layer, not kernel)
- Lifecycle service reads `WarmPolicy` from kernel registry — correct dependency direction

### Gap 2: Formal Worker Teardown — DONE

**Owner:** Codex
**Files created:**
- `sos/services/health/worker_teardown.py` — register, touch, prune workers
- `tests/test_worker_teardown.py` — tests passing

**Policy:**
- Workers registered in `~/.sos/state/workers.json`
- Worktrees stored in `~/.sos/worktrees/`
- Completed/failed/parked: cleaned after 30min grace
- Stale active: cleaned after 180min inactivity
- Teardown kills tmux session + removes git worktree
- Lifecycle calls prune every 10 cycles (10 min)

**Architecture compliance:**
- Worker teardown is a health service module — correct placement
- No kernel changes needed — workers are ephemeral, not part of identity layer
- Worktree paths under `~/.sos/` — consistent with existing state management

### Gap 3: Checkpoint on Context Compaction — TODO

**Owner:** Kasra
**Files to change:**
- `sos/services/health/lifecycle.py` — enhance compaction detection handler
- `sos/services/health/output_capture.py` — detect compaction patterns, trigger snapshot

**What changes:**
```
Step 1: Detect compaction
File: sos/services/health/lifecycle.py
Change: When compaction_patterns match in tmux output, call _snapshot_to_mirror()
Outcome: Compaction event triggers a Mirror save before context is lost

Step 2: Save working context to Mirror
File: sos/services/health/lifecycle.py (new function)
Change: Add _snapshot_to_mirror(agent_id) that POSTs to Mirror /store with:
  - current tasks (from Squad Service)
  - recent bus messages (from Redis stream, now correctly parsed via #92 fix)
  - last output snippet (from tmux capture)
  - working directory + git branch
Outcome: curl localhost:8844/search?query="kasra compaction" returns the snapshot

Step 3: Restore from Mirror on restart
File: sos/services/health/lifecycle.py (get_agent_context)
Change: If state file has no recent context, query Mirror for latest snapshot
Outcome: Restarted agent gets rich context even if state file was stale

Step 4: Add compaction hook (if possible)
File: ~/.claude/settings.json
Change: Add a hook that fires on compaction event and saves to Mirror
Outcome: Claude Code itself triggers the save, not just lifecycle detection
```

**Architecture compliance:**
- Lifecycle service → Mirror API via HTTP client (sos.clients.mirror) — correct pattern
- No kernel changes — compaction is a service concern
- Uses standard Message format for bus notifications of compaction events

## Bug Fixes — DONE

| Issue | Fix | Status |
|-------|-----|--------|
| #92 empty BUS MESSAGE | Parse `payload` JSON field in lifecycle.py | Fixed by sonnet subagent |
| #93 Stop hook not wired | Added Stop hook to settings.json | Fixed by sonnet subagent |
| code-review-graph broken | Reinstalled for Python 3.12, fixed .mcp.json | Fixed |
| settings.json absolute paths | code-review-graph hooks use full path | Fixed |
| nexus.md schema error | `status: alpha` → `coming-soon` | Fixed |
| Mirror port conflict | Killed stale process, restarted systemd | Fixed |

## Infrastructure — DONE

| Deliverable | File | Status |
|-------------|------|--------|
| Service mode config | `~/.sos/services.json` | Created (lean/full/budget) |
| Mode switcher | `~/scripts/sos-mode.sh` | Created |
| Project registry | `~/.sos/projects.json` + `sos/kernel/project_registry.py` | Created by Codex |
| Gemini Navigator setup | GEMINI.md, settings.json, delivery.py | Updated |
| Wake daemon routing | gemini agent + C-m submit | Updated, restarted |

## Blog Posts

| Post | File | Status |
|------|------|--------|
| What Is SOS | `mumega-site/content/en/blog/what-is-sos.md` | Written, build passes |
| Agent Harness Review | `mumega-site/content/en/blog/which-agent-harness-should-sos-adapt.md` | Written by Codex, build passes |

## Remaining Work

1. **Gap 3 implementation** — Kasra builds compaction checkpoint (steps 1-4 above)
2. **Dispatch guardrails** — Codex builds coordinator-only routing, no worker-to-worker
3. **sos-dev code review** — 8 issues need implementation (separate PR)
4. **Deploy blog posts** — `cd ~/mumega-site && npm run deploy`
5. **Restart lifecycle service** — pick up STUCK_MINUTES=120 and parked state changes
