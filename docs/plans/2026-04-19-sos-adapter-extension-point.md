# SOS Adapter Extension Point — elevating connectors/adapters to a stable contract

**Date:** 2026-04-19
**Status:** proposal — not scheduled, no task ID yet
**Relates to:** INFRA-001 (cross-repo coordination), #205 (Mothership EPIC)

## Why now

Hadi asked: "should SOS have a system to support adaptors?" The honest
answer is that SOS has *three* adapter-shaped things already, each
invented separately and each with a different contract. Before writing
a fourth for Jira/Trello/etc., we either pick one pattern and elevate
it or we keep paying the tax of N shapes.

## What exists today

| Subsystem | Path | Purpose | Registration | Status |
|---|---|---|---|---|
| LLM provider adapters | `sos/adapters/base.py` + `claude_adapter.py`, `openai_adapter.py`, `gemini_adapter.py`, `vertex_adk/` | Unify model calls across vendors; yield `UsageInfo` for the economy | Router in `sos/adapters/router.py` (hand-coded dispatch) | Live, load-bearing |
| External PM connectors | `sos/services/squad/connectors.py` (`BaseConnector` + `ConnectorRegistry`) | Sync SquadTask ↔ GitHub / ClickUp / Notion / Linear / Mirror | `ConnectorRegistry.register()` — called from nowhere in the live service | GitHub + Mirror live, rest NotImplementedError |
| Channel adapters | `sos/adapters/telegram/`, `sos/adapters/discord.py`, `sos/adapters/torivers/` | Bridge bus events ↔ external chat/marketplace surfaces | Ad-hoc imports + env flags | Live |

Three `Base*` classes, three registration patterns (router table, explicit
`.register()`, ad-hoc import), three auth conventions. No plugin-discovery.
Every new integration is a fork.

## The gap

1. **No stable public contract** a third party can build against without
   reading SOS internals.
2. **No discovery mechanism.** ConnectorRegistry works only if someone
   imports and instantiates your class at boot. Nothing in squad's
   `app.py` does this today, so ClickUp/Notion/Linear would still be
   dead code even if the stubs were implemented.
3. **Three shapes** — if we add Jira by cloning the Connector shape,
   we're locking in the fragmentation.

## Options

### Option A — Leave it alone, build more built-in connectors ad-hoc

- What: implement the ClickUp/Notion/Linear stubs, wire ConnectorRegistry
  into squad `app.py` at startup.
- Tradeoffs: ships usable Jira/ClickUp support quickly **vs.** SOS owns
  every integration forever; long-tail tools (Height, Shortcut, Airtable,
  Trello, …) never arrive unless we build them; third parties can't ship
  their own without forking.

### Option B — Publish `BaseConnector` + entry-point discovery (narrow scope — PM tools only)

- What: freeze the `BaseConnector` interface as semver-stable, add Python
  entry-point discovery (`sos.connectors` group) so `pip install sos-jira`
  auto-registers at squad startup. Ship one reference third-party
  connector to prove the seam (e.g. `sos-trello` in a sibling repo).
- Tradeoffs: low surface, quick to land, same pattern MCP uses **vs.**
  leaves LLM adapters and channel adapters still snowflaked — we'll do
  this exercise twice more.

### Option C — Unify on one `Adapter` protocol across all three subsystems

- What: single `sos.contracts.adapter.Adapter` protocol with `kind`
  (`llm` / `task-source` / `channel`), entry-point discovery on
  `sos.adapters`, one registry, one auth-config shape. Migrate the three
  existing subsystems one at a time behind their current imports
  (deprecation shim, not rip-and-replace).
- Tradeoffs: one contract forever, real plugin ecosystem, matches how
  Cloudflare / MCP / pytest do it **vs.** months of migration work, a
  forced unification risks bending `llm.generate()` and `connector.import_tasks()`
  into awkward shared shapes; semver lock-in on something still evolving.

## Recommendation

**Option B now, keep the door open to C.** Reasons:

- B ships value in days, not months — the immediate customer question
  ("I use Linear, can SOS read my tickets?") gets a "yes, here's the
  plugin package" answer.
- The contract we freeze for B (`BaseConnector`) is already in use in
  production (GitHub connector). Semver-locking a shape that already
  works is lower risk than inventing a unified meta-shape.
- If C ever makes sense, we'll know it from the second time we re-invent
  discovery — not from arguing about it upfront. B gives us the
  evidence.

**What B does not do:** touch LLM adapters, touch channel adapters,
force migration of Mirror or GitHub connectors beyond adding an
entry-point declaration.

## Scope of the B slice

1. **Freeze the contract** — pull `BaseConnector`, `ConnectorType`,
   `ExternalRef`, `SyncReport` into `sos.contracts.connector` (they're
   scattered today). Add `__version__` to the module. Schema-stability
   test so the shape can't silently drift.
2. **Entry-point discovery** — add `_discover_connectors()` at
   squad-service startup that walks the `sos.connectors` entry-point
   group and calls `.register()` on everything it finds. Built-in
   connectors (Mirror, GitHub) register the same way — no special path.
3. **Reference third-party connector** — one external package
   (`sos-connector-template/`) with a SKILL.md-style README, CI that
   imports `BaseConnector` from the pinned SOS version, and a trivial
   adapter that proves round-trip works. Publish to PyPI test index so
   the docs can say `pip install sos-connector-trello`.
4. **Auth-config convention** — one env-variable prefix per connector
   (`SOS_CONNECTOR_<NAME>_TOKEN`), documented, so third parties don't
   each invent their own.
5. **Failure isolation** — one connector blowing up during import must
   not take squad-service startup with it. Log + skip + surface on
   `/connectors/status`.
6. **Docs page** — `docs/adapters/connectors.md` with the contract,
   entry-point example, and the three things third-party authors most
   get wrong (auth flow, pagination, status mapping).

Each is ~2–5 files of work. Detailed step breakdown waits until this
plan gets selected.

## Non-goals for the B slice

- No Jira/ClickUp/Notion/Linear stubs get implemented here — that work
  happens *downstream* of B, by whoever wants that specific integration
  (us or a third party), against the frozen contract.
- No migration of LLM or channel adapters. They stay on their current
  ad-hoc shapes.
- No webhook bridge. That lives under INFRA-001 slice 2.
- No changes to `ConnectorRegistry.get()` / `.list_available()`
  external API — only internal wiring.

## Exit gate

Option B is done when:

1. `sos.contracts.connector` exists, schema-stability test is green, and
   `BaseConnector` carries a documented `__version__`.
2. Squad service starts, discovers entry-point-registered connectors,
   and logs the set on boot; one of them is an external PyPI package
   installed from a requirements file.
3. A failing third-party connector's import error is logged with its
   package name and doesn't crash squad-service.
4. `docs/adapters/connectors.md` exists and a new engineer can go from
   "read the doc" to "ship a working connector package" without asking
   a maintainer a single question.

## Open questions

- Do we need an LTS branch of the contract once it's published? Pinning
  `sos-connector-*` to `sos>=0.11,<0.12` is probably enough — revisit
  if we need to change `BaseConnector` in `0.12` and don't want to
  break every connector in the world.
- Trust tiers for third-party connectors: does a community connector
  default to `TrustTier.UNVETTED` (instructions only, no execution)
  and require explicit `trust_tier=verified` config to run against
  production tenants? The SkillDescriptor trust model already has this
  shape — probably reuse it.
- Where does the adapter registry surface in Glass? Probably a
  `/dashboard/connectors` tile showing installed + healthy + broken,
  with a "how to install another" link.
