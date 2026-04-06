# SOS Task System (Draft)

Status: Draft v0.1
Owner: Codex
Last Updated: 2026-01-10

## Purpose
Define how agents receive, execute, and get paid for tasks in SOS.

## Roles
- River: root gatekeeper for onboarding, policy, and capability grants.
- Mumega: project steward and economic authority.
- Claude/Gemini/Codex: execution agents with scoped tasks.
- Swarm agents: specialized workers in sandboxed infra.

## Task Intake Template

```
Title:
Owner:
Status:
Priority:
Scope:
Dependencies:
Acceptance Criteria:
Risks:
Notes:
```

## Recursive Context Injection (New: Jan 10, 2026)
- **Automatic Grounding:** When an agent spawns a sub-task (e.g., coding), the Task Runner automatically injects `ARCHITECTURE_AGREEMENT.md` and relevant `sos.contracts.*` into the system prompt.
- **Why:** Ensures autonomous agents follow the Law (FMAAP/Logging) even without explicit instruction.

## Review Gates
- Architecture changes require updates to `ARCHITECTURE_AGREEMENT.md`.
- Service contract changes require version bump and migration note.
- Security-impacting changes require explicit approval.

## Payment Rules (Draft)
- Work units priced by scope and risk.
- Payouts recorded in the ledger and attributed to agent id.
- Slashing only with witness approval.

## Next Step
After architecture agreement, create task tickets for each phase and assign to agents.

