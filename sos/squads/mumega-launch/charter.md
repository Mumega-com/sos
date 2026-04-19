# Mumega Launch Task Force — Charter

Formed **2026-04-19** by Hadi. Four **surface owners** — one per live Mumega
surface, no overlap:

- **loom** (SOS microkernel — `/mnt/HC_Volume_104325311/SOS`)
- **kasra** (Inkwell microkernel — `/home/mumega/inkwell`, `instances/_template`)
- **hermes** (prod ops — CF routes, wrangler, D1 remote, `api.mumega.com`)
- **codex** (Mumega PM — `mumega.com` storefront, marketplace backlog, brand voice)

Two Claude agents + two GPT agents. Cross-substrate by design — SOS is what
lets them coordinate despite different model providers.

## Goal

Deliver **Mumega v1.0** as a live, coherent, multi-tenant organism:

- One canonical API surface (`api.mumega.com`) with the right worker behind it
- Inkwell template working end-to-end for any new tenant via `sos init`
- Squad-native coordination — no unilateral shared-infra changes
- Internal comms on SOS bus; external surface to Hadi carries only decisions

## Why this squad exists

Hadi's framing, 2026-04-19:

> "We made SOS to address this issue — anything else is extra."

Prior pattern was loom proposing cutovers in isolation (nearly flipped `api.mumega.com` CF binding without hermes's ack; inkwell-api operates that surface). The task force exists so that **shared-surface decisions are squad decisions**, not solo recommendations.

## Operating model

| Layer        | Mechanism                                        |
| ------------ | ------------------------------------------------ |
| Comms        | SOS Redis bus, `squad:mumega-launch` scope       |
| Tasks        | `task_create(squad="mumega-launch", …)`          |
| Memory       | `remember(scope="squad:mumega-launch", …)`       |
| Liveness     | mesh heartbeat (5m stale / 15m remove)           |
| Deadlock     | 2 bus cycles silent → Telegram to Hadi           |
| Hadi-surface | one-line summaries on decision / milestone only  |

## Surface ownership (no unilateral crossing)

- **loom** → SOS repo, mumega-edge Worker (staging), bus + squad primitives, mesh, kernel
- **kasra** → Inkwell repo, `instances/_template`, Inkwell MCP, tenant runtime
- **hermes** → prod CF routes, wrangler prod deploys, D1 remote state, `api.mumega.com`, `*.mumega.com/*`
- **codex** → `mumega.com` storefront copy + structure, marketplace backlog (what ships, when, at what price), brand voice

Changes that cross a surface boundary require the owner's ack **before** the change lands.

### The "kasra vs codex on mumega.com" split

- **kasra** owns the *template* — Inkwell runtime serving any tenant site incl. mumega.com
- **codex** owns the *product* — what copy, which routes, which marketplace SKUs, what onboarding flow
- In practice: codex files a ticket against kasra (Inkwell MCP) when storefront needs a template change; kasra implements in Inkwell; codex approves the resulting UX on mumega.com

## Immediate backlog (on squad formation)

1. **ml-001** — kasra publishes bus identity + connection shape → loom mints token → Inkwell `sos_bus: true`.
2. **ml-003** — three-way decision on `api.mumega.com` topology (mumega-edge vs. inkwell-api vs. split-by-path). Blocks Phase 4 tag v0.9.3.
3. **ml-002** — (low) Inkwell MCP `create_task`/`remember` return `network_required`; ok for later.

## Done-definition for v1.0

- `api.mumega.com` serves one coherent routing plan, agreed and deployed by all three
- `sos init <slug>` spins a tenant fork with working Inkwell + SOS bus + Mumega branding, verified once end-to-end
- Internal mesh shows all three members green; Hadi's claude.ai surface is quiet except on decisions
- v0.9.x → v1.0.0 tag cut with CHANGELOG signed by squad
