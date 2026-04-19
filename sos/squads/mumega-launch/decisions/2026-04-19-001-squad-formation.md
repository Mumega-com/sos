# Decision 001 — Squad formation

- **Date**: 2026-04-19
- **Status**: RATIFIED (Hadi 2026-04-19)
- **Scope**: `squad:mumega-launch`

## Decision

Form the Mumega Launch Task Force as a four-agent squad of **surface owners**.
Every member owns a distinct production surface; no one has overlapping
authority on the same surface.

| Agent  | Runtime                          | Surface                                          |
| ------ | -------------------------------- | ------------------------------------------------ |
| loom   | claude-opus-4-7 / tmux           | SOS kernel, bus, squads, mesh (microkernel)     |
| kasra  | claude / tmux                    | Inkwell repo, `instances/_template`, MCP        |
| hermes | openai-gpt-5.4 / ops MCP         | Prod CF routes, wrangler, D1 remote, edge      |
| codex  | codex-cli (GPT) / tmux           | mumega.com storefront + marketplace product     |

## Rationale

Prior pattern was per-agent decisions affecting shared surfaces
(api.mumega.com cutover was nearly flipped unilaterally). This squad makes
**shared-surface decisions into squad decisions**, enforced by an explicit
ownership table.

Four owners because Mumega has four live surfaces — SOS (substrate),
Inkwell (template kernel), prod ops (edge/routes/deploy), and the
storefront product (mumega.com + marketplace UX). Collapsing any two
re-creates the bottleneck this squad was created to remove.

## Comms topology

- Internal agent↔agent: SOS Redis bus, scope `squad:mumega-launch`
- Squad timeline mirror: Discord `#agent-collab` (Hadi read-only channel)
- External Hadi-surface: Telegram (decisions/milestones/blockers only)

## Cross-substrate note

Two Claude agents (loom, kasra) + two GPT agents (hermes, codex). SOS is
the shared substrate that lets them coordinate despite different model
providers — this is SOS dogfooding itself.
