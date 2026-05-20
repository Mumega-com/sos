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

S075-S080 are complete. S081 is next.

| Priority | Sprint | Work | Exit criteria |
|---:|---|---|---|
| 1 | S081 | Plugin/profile boundary pass | OpenClaw/Hermes-style hosts have a documented adapter contract and one smoke proof |
| 2 | S082 | Thin private internal SOS | internal repo becomes deployment continuity and host overlay, not a kernel fork |
| 3 | S083 | Retire compatibility shims | temporary extraction shims are removed, migrated, or explicitly time-boxed |
| 4 | S084 | Live service migration train | each host service has public-kernel unit, proof, rollback, and owner issue |
| 5 | S085 | Operator loop and observability | agents, bus, tasks, repos, issues, releases, and health have one repeatable runbook and operator view |
| 6 | S086 | Maintainability split | MCP monolith starts shrinking behind contract tests without changing external tool behavior |
| 7 | S087 | Public release pass | taggable release candidate: license, contribution policy, examples, CI, and residual-risk issues complete |
| 8 | Later | Federation V0 design | two trusted SOS nodes can exchange one bounded task without sharing private runtime state |

## S077 Baseline

- SOS public clone: 2731 tests, 0 collection errors after S079 local-profile
  coverage.
- Mirror public clone: 145 passed, 2 skipped.
- Inkwell public clone: `npm install` and `npm run build` exit 0.

Do not spend S078 re-proving standalone cleanliness unless a new public-kernel
change lands.

## S079 Local Install Target

The public first-run path is:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
scripts/sos-local-dev.sh up
scripts/sos-local-dev.sh doctor
```

The doctor must prove Redis, MCP, bus send/inbox, task create/claim/complete,
and Mirror-disabled behavior without editing private token files.

S079 result: implemented in the public profile. The helper generates local dev
tokens, selects free local ports, starts isolated Redis/bus/MCP/Squad services,
runs Squad migrations through public revision `0023`, and verifies the doctor
flow above.

## S080 Security Edge Result

The public security model lives in `docs/security/`:

- `public-edge-map.md` lists intended host/path/upstream/auth/exposure posture.
- `threat-model.md` defines health, webhook, CORS, Redis, and residual-risk
  policies.
- `public-route-checklist.md` is the gate checklist for any new public route.

The public release gate now verifies these docs exist, and `sos doctor` reports
unsafe production Redis configuration.

## Operating Rule

Public kernel changes go to `Mumega-com/sos` from a clean public clone.

Host-specific product, customer, billing, and deployment code belongs in host
overlays, not in public SOS.
