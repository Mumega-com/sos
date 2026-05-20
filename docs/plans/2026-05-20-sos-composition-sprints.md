# SOS Composition Sprint Plan

Status: active, canonical coming-sprint queue
Last updated: 2026-05-20

## North Star

SOS is a self-hosted coordination kernel for heterogeneous agents. A real
operator can run Claude Code, Codex, Gemini workers, OpenClaw, Hermes, scripts,
and humans on one bus with shared identity, tasks, memory, handoff, and
verification.

Mumega is one living proof environment. Public SOS is the reusable kernel.

## Target Composition

```text
public SOS kernel
+ host add-ons
+ private deployment config
= a host-specific multi-agent operating loop
```

## Coming Sprint Queue

S075-S077 are complete. S078 is active.

| Priority | Sprint | Work | Exit criteria |
|---:|---|---|---|
| 1 | S078 | Public kernel release gate | public docs, versions, changelog, and boundary CI agree with standalone-clean public SOS |
| 2 | S079 | Public install and doctor pass | a stranger can clone, start Redis/MCP, send/inbox, create/complete one task, and run doctor |
| 3 | S080 | Security and public edge pass | public route map, threat model, health policy, webhook policy, CORS, and Redis config checks are explicit |
| 4 | S081 | Plugin/profile boundary pass | OpenClaw/Hermes-style hosts have a documented adapter contract and one smoke proof |
| 5 | S082 | Thin private internal SOS | internal repo becomes deployment continuity and host overlay, not a kernel fork |
| 6 | S083 | Retire compatibility shims | temporary extraction shims are removed, migrated, or explicitly time-boxed |
| 7 | S084 | Live service migration train | each host service has public-kernel unit, proof, rollback, and owner issue |
| 8 | S085 | Operator loop and observability | agents, bus, tasks, repos, issues, releases, and health have one repeatable runbook and operator view |
| 9 | S086 | Maintainability split | MCP monolith starts shrinking behind contract tests without changing external tool behavior |
| 10 | S087 | Public release pass | taggable release candidate: license, contribution policy, examples, CI, and residual-risk issues complete |
| 11 | Later | Federation V0 design | two trusted SOS nodes can exchange one bounded task without sharing private runtime state |

## S077 Baseline

- SOS public clone: 2723 tests, 0 collection errors.
- Mirror public clone: 145 passed, 2 skipped.
- Inkwell public clone: `npm install` and `npm run build` exit 0.

Do not spend S078 re-proving standalone cleanliness unless a new public-kernel
change lands.

## Operating Rule

Public kernel changes go to `Mumega-com/sos` from a clean public clone.

Host-specific product, customer, billing, and deployment code belongs in host
overlays, not in public SOS.

