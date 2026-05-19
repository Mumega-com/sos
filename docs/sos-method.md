# SOS Method — how we build

Hard rules for every edit in this repo. Machine-enforced via
import-linter + pre-commit.

## The boundary tree

- `kernel/`     minimal trusted core (bus, auth, health, policy, config)
- `contracts/`  ABI: JSON Schemas + Pydantic + OpenAPI
- `services/`   one process per service; never imports another service
- `clients/`    HTTP SDK per service; imports contracts only
- `cli/`        end-user chat app
- `adapters/`   outbound integrations (Discord, Telegram, CrewAI, ToRivers)
- `mcp/`        MCP protocol bridge (thin proxy)
- `agents/`     per-agent genesis / seed files
- `bus/`        bus config (`tokens.json`) — data, not code
- `skills/`     skill registry content
- `deprecated/` retired code, walled off

## Rules (enforced)

- **R1** `services/<A>/` never imports `services/<B>/`. Cross goes via
  `contracts/` or bus/HTTP.
- **R2** `clients/`, `adapters/`, `mcp/`, `cli/`, `agents/` may import
  `contracts/` only.
- **R3** Bus producers and HTTP handlers build messages via
  `contracts.*` Pydantic — never hand-rolled dicts.
- **R4** `scripts/` is ops-only; product flows live with their service.
- **R5** `deprecated/` and `.archive/` are walled off; no live imports.
- **R6** `tests/contracts/` imports `contracts.*` only; cross-service
  integration tests live under `tests/integration/` or `tests/services/`.

## Before you edit

- Check `docs/plans/2026-04-17-sos-structural-audit.md` for known
  violations.
- Check `docs/plans/2026-04-17-sos-sprint-roadmap.md` for what sprint
  owns your area.
- If your edit would cross a boundary, reshape it first — don't cheat.

## Enforcement layers

1. **This document** — read before editing.
2. **Per-agent contract** — each `.claude/agents/*.md` file points here.
3. **import-linter** — `lint-imports` runs locally and in CI.
4. **pre-commit hook** — `pre-commit install` wires it to your local
   commits.

## Activating locally

- `pip install pre-commit import-linter` (or add to your dev env).
- `pre-commit install` inside your clone.
- Any commit that breaks a boundary is rejected at the hook, not at
  review.

## The ignore list

`pyproject.toml` [tool.importlinter.contracts] ignore_imports carries
pre-existing violations that a named sprint closes. Shrinking it is
the goal — do not extend it without a sprint entry in the roadmap.
