# SOS OSS extraction — when and how

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Recommendation
**Depends on:** v0.9.0 Frozen (no OSS release before contracts are stable)
**Related to:** Mumega product roadmap Phase 5 "Go Viral"

## The question

Today `Mumega-com/sos` is a public GitHub repo but its surface area is Mumega-specific: tenant registry, Stripe, billing, mumega-edge integration, branded CLI tool name (`mumega`), Mumega-specific plans/memos. Those leak business-layer concerns into what we'd want to be a kernel-layer open source project.

When do we extract? How?

## Three possible shapes

### Shape 1 — Monorepo with public-ready prefix

Current. `Mumega-com/sos` contains both kernel-level code and Mumega-business code. Public can read it, fork it, but they also see Stripe webhooks, Mumega onboarding flows, TROP-specific bits.

- **Pro:** one repo, no split overhead, Kasra + sos-dev + codex all work in one place
- **Con:** forks inherit Mumega business logic they don't need, open-source narrative is muddled

### Shape 2 — Extract `sos-core` as separate public repo

At some trigger (v0.9 Frozen or earlier if needed), create `Mumega-com/sos-core` (or just `sos`) that contains only:
- `sos/kernel/` (Bus, Auth, Registry)
- `sos/bus/`, `sos/services/bus/`, `sos/services/squad/`, `sos/services/mirror/`, `sos/services/health/`
- `sos/services/providers/` (Provider Matrix from v0.4.1)
- `sos/contracts/` (schemas)
- `sos/mcp/` (MCP server)
- `sos/clients/` (client SDKs)
- Plus adapters that aren't Mumega-specific (Claude/Gemini/OpenAI/Discord/Telegram/CrewAI/LangGraph/Vertex)

What stays in `Mumega-com/sos` (or rename to `mumega`):
- `sos/services/saas/` (tenant registry, Stripe, billing — Kasra's layer)
- `sos/cli/onboard.py` (Mumega-specific customer provisioning)
- `sos/services/content/` (content pipeline)
- Mumega-specific adapters (torivers, sitepilotai integration)
- Mumega plans, mumega-docs links, mumega branding

- **Pro:** clean open-source story, forks only get what they need, Mumega's business layer stays private-ish (public repo but not "the SOS project")
- **Con:** double-repo maintenance, internal imports become cross-repo imports (need to publish sos-core to PyPI first)

### Shape 3 — Keep public Mumega-com/sos, add a `sos-core/` directory convention

Inside the same repo, declare `sos/kernel/`, `sos/bus/`, `sos/contracts/`, `sos/services/{squad,mirror,providers,health}/` as the "core" and everything else as Mumega-business. Document the split via CODEOWNERS + README. Maintain invariant: core imports nothing from outside core.

- **Pro:** no repo split, but enforced boundary; faster to ship than Shape 2
- **Con:** weaker than a true split. Third parties still see business code; harder to communicate "just use the core."

## Recommendation: **Shape 3 now, Shape 2 at v0.9**

### Shape 3 implementation (this quarter)

1. **Directory-level invariant.** Core = `sos/kernel/`, `sos/bus/`, `sos/contracts/`, `sos/services/{squad,mirror,health,providers,bus}/`, `sos/mcp/`, `sos/clients/`, `sos/adapters/{claude,gemini,openai,discord,telegram,crewai,langgraph,vertex_adk}/`. Everything else is Mumega business.

2. **Import boundary enforced in CI.** Add `tests/test_import_boundary.py` that parses every core file's imports and fails CI if any import points outside core.

3. **CODEOWNERS.** Core is owned by sos-dev + codex. Mumega business is owned by kasra. PRs touching both require both owners.

4. **README update.** "SOS is open source. This repo contains the SOS core (MIT) alongside Mumega's business layer. Forks can consume only `sos/` core subdirectories. The `mumega/`-prefix code is Mumega-specific and runs on the same infra but isn't part of the OSS distribution."

5. **Public package.** At v0.5 or v0.6 (contracts stable enough), publish `pip install sos` that is the core-only slice. `mumega` can stay not-pipable since it's company-specific.

### Shape 2 at v0.9 Frozen (cutover point)

1. **Extract** `Mumega-com/sos-core` as a fresh public repo containing exactly what `pip install sos` ships. Move core history if feasible; squash-commit if not.

2. **Mumega-com/sos becomes `Mumega-com/mumega`** — the business layer, private-friendly, imports `sos-core` as a dependency.

3. **`Mumega-com/sos` repo becomes `Mumega-com/sos-core`** (public) — or we archive the current repo and start fresh (cleaner history but loses context).

4. **Public release + launch** — HN, blog, SDKs published, Inkwell fork guide, demo video (per Mumega roadmap Phase 5).

### Why this order

- Shape 3 now = cheap. Mostly directory discipline + CI check + docs. Buys us time to see which code truly ended up in core.
- Shape 2 at v0.9 = correct moment. Contracts are frozen, so the core's API surface is stable. Public launch happens on stable code, not a 0.x kernel.
- Avoids **premature extraction** — if we did Shape 2 today, we'd discover things we thought were core actually need business-specific shims, and have to reshuffle.

## What gets public vs. proprietary

| Component | OSS-ready? | Reason |
|---|---|---|
| Kernel (Bus, Auth, Registry) | ✅ | Generic, reusable, protocol-level |
| Bus (Redis streams, delivery, wake daemon) | ✅ | Pure coordination, no business logic |
| Squad service | ✅ | Tenant-agnostic design; Mumega-specific squads are *configuration*, not code |
| Mirror | ✅ | Already OSS on `Mumega-com/mirror` |
| Providers matrix (v0.4.1) | ✅ | Pure LLM routing, no Mumega-specific |
| MCP server | ✅ | Generic MCP impl |
| Agent Cards + message schemas + Provider Cards + Breakable Cards | ✅ | Contracts are the OSS story |
| Contracts (OpenAPI, error taxonomy) | ✅ | Designed for cross-language/cross-impl portability |
| SaaS service (tenant, Stripe, billing) | ❌ | Mumega-specific customer flow, Stripe integration, branded endpoints |
| onboard.py CLI | ❌ | Mumega's provisioning flow (Linux users, specific paths) |
| Content service | ❌ | Mumega-specific blog pipeline |
| ToRivers adapter | ❌ | Mumega marketplace integration |
| mumega-edge Worker | ❌ | Mumega-specific auth flow |
| Inkwell integration | ❌ | Mumega product |
| Plans in `docs/plans/` | ❌ | Internal strategy |
| $MIND Solana code | ✅ or ❌ (depends) | If spec is public (WHITEPAPER.md already is), code can be too; if mainnet tokens are live, keys stay private |
| Stats & telemetry UI | ❌ | Product, not kernel |

Roughly 60% OSS-ready today, 40% Mumega-proprietary. That ratio holds across versions.

## Licensing

| Component | License |
|---|---|
| SOS core | **Apache 2.0** or **MIT** (permissive — maximizes adoption) |
| Mumega business | **BUSL 1.1** or **proprietary** (current `mumega-cli` uses BSL 1.1 per mumega-docs) |
| WHITEPAPER.md | CC-BY (for $MIND protocol) |

**Recommendation:** Apache 2.0 for SOS core. Permissive, enterprise-friendly, includes patent grant which matters for Provider Matrix and crypto-adjacent code.

## Ongoing maintenance model

- **SOS core:** accepts community PRs, has issue templates, CONTRIBUTING.md, responds on a schedule
- **Mumega business:** internal team, no external PRs, public-readable but not community-collaborative
- **Split CI:** core has its own CI that must pass independently of business code

## The "Inkwell moat" question

Is there a risk that open-sourcing SOS lets competitors build the same product?

**No.** The moat is:
- $MIND tokenomics + community
- Agent DNA + fine-tuned Gemma per tenant (Phase 6)
- Inkwell's commerce integration + customer relationships
- Data (30+ days of tenant operational history compounds into the 16D DNA vectors)

The kernel being OSS **increases** moat because adoption multiplies and the Mumega deployment becomes the reference implementation that others build against.

## Decisions open for Hadi

| # | Question | Default |
|---|---|---|
| OSS1 | Shape 3 now (directory discipline + CI) in v0.4.x? | Yes, cheap |
| OSS2 | Shape 2 extraction (separate repos) at v0.9? | Yes, stability-gated |
| OSS3 | Apache 2.0 for SOS core? | Yes, unless you prefer MIT |
| OSS4 | BUSL or proprietary for Mumega business? | BUSL 1.1 (matches existing mumega-cli) |
| OSS5 | When to rename `Mumega-com/sos` → `Mumega-com/sos-core`? | At v0.9 extraction; or keep as `sos` if we don't split |

## One-line summary

Shape 3 (directory + CI invariant) now in v0.4.x. Shape 2 (repo split) at v0.9 Frozen, aligned with Mumega Phase 5 Go Viral launch. Apache 2.0 for core, BUSL 1.1 for business. ~60% of current codebase is OSS-ready. Moat is tokenomics + data + product, not code secrecy.
