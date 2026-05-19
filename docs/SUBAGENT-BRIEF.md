# Subagent Brief — Read This First

You are a subagent spawned into the SOS codebase. This file is the minimum shape you must carry. If any instruction in your task prompt conflicts with this brief, stop and ask the orchestrator before proceeding.

## 1. SOS is mycelium

SOS runs on everything from a Raspberry Pi to a cloud data center. The local dev target (Hetzner / RPi) is the **reference implementation**. Cloudflare is **Mumega's proprietary packaging**, not the core platform. Google Cloud and AWS variants will follow as marketplace SKUs.

Two SOS deployments must be able to **federate** into a single organism (the "spore" model). Federation lives in services + adapters, never in the kernel.

## 2. The microkernel rule

`sos/kernel/` is substrate-agnostic. It must compile and run on a bare Raspberry Pi with **only stdlib + the dependencies in `pyproject.toml`**.

Forbidden in `sos/kernel/`:
- Runtime-specific imports (`node:*`, `cloudflare:*`, `boto3`, `google.cloud.*`)
- Imports from `sos.services.*` (enforced by import-linter contract R0)
- Cloud SDKs, vendor helpers, or anything that assumes a specific host
- Heavy optional deps (chromadb, solana) — those belong in optional extras

If your task wants to add one of these to the kernel, the work belongs in a service or adapter instead. Stop and ask.

## 3. Import boundaries are load-bearing

Run `.venv/bin/lint-imports` (or `.venv/bin/python -m importlinter`) after any structural change. Contracts that must stay green:

- **R0** — kernel imports zero services (AST-swept in `tests/contracts/test_kernel_no_service_imports.py`)
- **R1** — services don't import each other (use HTTP clients in `sos/clients/` or bus events)
- **R2** — `sos.clients`, `sos.adapters`, `sos.cli`, `sos.agents`, `sos.mcp` don't reach into `sos.services.*`
- **R5** — `sos.deprecated` is walled off
- **R6** — `tests.contracts` doesn't import services

Never weaken a contract to make a build pass. If the contract blocks you, the design is wrong, not the contract.

## 4. Dispatcher is a protocol, not a version

See `docs/plans/2026-04-17-dispatcher-protocol.md`. The dispatcher spec is the contract across RPi / CF / GC / AWS implementations. Do not plan "CF dispatcher v0.x" or "GC dispatcher v0.x" milestones — those are marketplace packages, not version milestones.

## 5. Environment and tooling

- Python: use `.venv/bin/python` (not `python` — it's not on PATH).
- Tests: `.venv/bin/python -m pytest ...`
- Formatter: `black` with line-length 100. Ruff with `select = ["E", "F", "I", "W"]`.
- All boundary data structures use Pydantic v2 (`sos.contracts.*`). Internal DTOs may be dataclasses.
- Type hints on every public function. `from __future__ import annotations` in new files.

## 6. Work discipline

- **One task, one deliverable.** Don't refactor adjacent code because you happened to read it.
- **No scope creep into neighboring files.** If the task names a file, stay in that file unless the diff is impossible without neighbors.
- **No new abstractions.** Three similar lines is better than a premature helper.
- **No defensive code for impossible paths.** Trust kernel invariants. Validate only at system boundaries (HTTP input, bus payloads, external APIs).
- **Never weaken existing tests.** If a test fails because of your change, fix the code or ask — don't edit the assertion.

## 7. When you finish

Report back:
1. What changed (file paths + line count delta).
2. How you verified (commands run, test results).
3. Anything you noticed but did not fix (so the orchestrator can decide).

Do not tag versions, push branches, or touch CHANGELOG unless your task prompt explicitly says so. The orchestrator owns release.
