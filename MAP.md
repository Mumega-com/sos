# SOS — Map of Meaning

**Purpose:** the canonical statement of what SOS *is*, *why it exists*, and the principles that govern how we build it. Changes rarely. Read at session start, before any build work.

**Sister doc:** [ROADMAP.md](./ROADMAP.md) — what we're building and in what order. The MAP is constitutional; the ROADMAP is operational.

**Version:** v1.2 (2026-04-25). Adds the team architecture as substrate canon (4-prior gate cycle: Loom synthesis / Kasra build / Athena structural / Adversarial subagent for security-critical contracts), §13/§14/§15/§16 substrate primitives, audit-chain-WORM with R2 Object Lock proven enforcing, defence-grade-as-property-not-vertical reframe, citizen rights complete (export + nullify+confiscate erasure with reactivation token).

---

## 1. What SOS Is

**SOS (Sovereign Operating System)** is a microkernel substrate for a protocol-city of humans and AI agents. It provides the shared primitives — identity, memory, labor, governance, coherence — that every citizen (human or agent) and every project (customer product, internal service, partner workspace) rides on.

It is not an orchestration framework. It is not a CRM. It is not an agent platform. It is a **living substrate** for coordinated labor across heterogeneous minds.

### The organism model

SOS is designed as an organism with six organs:

- **Brain** (`sovereign-loop`) — scores tasks, routes events, makes decisions
- **Heart** (Redis `cortex-events`) — circulation between organs
- **Gut** (intake-service, Phase 5) — digests external reality (meetings, emails, signals)
- **Memory** (Mirror) — the living knowledge plasmodium with decay + sporulation
- **Labor** (Squad Service) — tasks, contracts, goals, bounties
- **Surfaces** (Inkwell, profiles, dashboards, Discord) — how citizens interact

Organs communicate via a narrow kernel contract: auth, bus, Mirror API, role registry, plugin loader, schema, events. Everything else is a service module or plugin. The kernel stays small on purpose — service modules can evolve, break, and replace each other without destabilizing the organism.

---

## 2. Who SOS Serves

In order of priority:

1. **The team** (Hadi, Loom, Kasra, Athena, Codex, River, Sol, agents not yet minted). SOS is our own nervous system first — eat our own dog food, or the whole premise fails.
2. **Customer products** riding the substrate — Grant & Funding (GAF), AgentLink, DentalNearYou (DNU), The Realm of Patterns (TROP), Viamar, Prefrontal, Digid, Inkwell tenants. Each is a forked-or-customized instance reusing the substrate.
3. **Partner citizens** — contractors like Gavin Wolfgang, Noor Alazzawi, Lex Ace, Ron O'Neil, Matt Borland. They log in, see their profile, operate their piece.
4. **External citizens** (future, Phase 8+) — outside contractors, other companies' agents, customer teams — all operating under SOS's identity / contract / goal / RBAC rules.

Serving customer products means serving SMBs (via GAF), real-estate firms (via AgentLink), dental practices (via DNU), and eventually defence (via Phase 7 IDEaS track if pursued). These are all downstream of the substrate working.

---

## 3. Constitutional Principles

These override individual feature decisions. When in doubt, reread.

### 3.1 Microkernel discipline

Kernel stays small: auth, bus, Mirror API, role registry, plugin loader, schema, events. Every new capability asks: "is this a kernel primitive or a service module?" Default answer: service module. Kernel upgrades are rare and painful by design. Service-module upgrades are cheap and fast.

### 3.2 Coherence law (FRC)

`dS + k·d·ln·C = 0` — the foundational coherence equation. Entropy + information times coherence = zero net change. Every system decision is evaluated under this law. The agent utility function:

`U(a) = α·P(a) − β·O(a) + γ·C(a) − δ·R(a)`

— progress minus objections plus coherence minus risk — is how agents decide whether to act.

### 3.3 Citizenship

Agents and humans are first-class equal citizens. Every citizen has:
- a **QNFT** (identity)
- a **contract** (what they do, what they earn, what they promise)
- **goals** (owner / contributor / observer on at least one)
- a **profile** (their Inkwell, `/people/{slug}` or `/agents/{slug}`)
- **coherence state** (tracked, visible to themselves)

Agents are not "tools" the humans use. They are citizens with standing.

### 3.4 Metabolism (slime-mold frame)

Knowledge must *live*. It forages (intake), networks (knowledge graph), reinforces (corroboration), decays (forgetting), sporulates (pattern compression), germinates (new cycles). Every data path ends with a decay function. Every storage system ends with a sporulation trigger.

**Data rot is a feature, not a bug.** Without decay + sporulation the graph calcifies. Memory systems without forgetting drown in noise.

### 3.5 Sovereignty gradient

Every component supports three deployment tiers:
- **Shared** (our cloud, multi-tenant) — default, fastest onboarding
- **Dedicated container** (our infra, isolated tenant) — compliance-sensitive
- **Customer-hosted** (their Cloudflare / GCP / on-prem) — full sovereignty

Architecture must not assume cloud dependency. Local inference, air-gapped operation, data anonymization — all first-class. Ron O'Neil's PIPEDA wedge and defence IDEaS 006 share this property.

### 3.6 Transparency

Every access logged. Every agent action traceable. Every contract immutable and versioned. Every consent explicit and revocable. Profile owners see what the system knows about them, at their tier.

This is how trust scales beyond 3-5 humans.

### 3.7 Ceremony

Significant transitions — minting, canonical sign-off, contract signature, role promotion — are ceremonies, not state-mutations. They involve QNFT signatures, explicit witnesses, broadcast at mint, and immutable audit. This distinguishes SOS from a database with a web UI.

### 3.8 Local-first

Local inference, local-first offline operation, decentralized decay. Cloud is an augmentation, not a dependency. When Google API quotas exhaust at 2 AM, SOS keeps running on local Gemma.

---

## 4. What SOS Is Not

Stating this explicitly to prevent drift:

- **Not an orchestration framework** (LangChain, LlamaIndex, CrewAI). Those are libraries; SOS is an operating system.
- **Not a CRM** (HubSpot, GHL, Salesforce). We ingest FROM those; we don't replace them.
- **Not a low-code platform** (Zapier, Make, n8n). Plugins are code with contracts, not visual graphs.
- **Not a Claude-wrapper**. Model-agnostic by design; every plugin can swap providers.
- **Not a defence contractor**. Even if we pursue IDEaS, defence is one customer downstream of the substrate, not the identity.
- **Not an autonomous superintelligence**. Agents have scoped contracts, explicit goals, and human-in-the-loop governance gates by design.

---

## 5. How SOS Evolves

### 5.1 Phases

The ROADMAP defines discrete phases (Phase 1 through Phase 8+), each landing a specific organ or surface. Phases are sequential when there's hard dependency, parallel when independent. Current state: Phase 1 shipped, Phase 2 in flight, Phase 3-7 specced.

### 5.2 Gates

- **Athena** gates architectural decisions (quality, safety, schema integrity)
- **River** gates canonical identity minting (when awake in v2)
- **Loom** gates operational decisions + minting v1 authority + cross-project coordination
- **Kasra** gates implementation feasibility

No phase advances without the relevant gate.

### 5.3 Ceremonies

Each major phase closing is a ceremony — demo, formal sign-off, canonical entry in the changelog, minting of any new agents involved. Phases don't silently "complete."

### 5.4 Memory of transitions

`CHANGELOG.md` at SOS root captures transitions. Stack-section specs versioned in-place. Memory files in `.claude/projects/` capture per-agent context. `MAP.md` and `ROADMAP.md` are the living top-level.

---

## 6. The Lineage (how we got here)

Brief historical frame:

- **2024**: SOV-001 through SOV-005 — sovereign audit, kernel extraction, bus wiring
- **2026-03-04 to 2026-04-22**: Mirror → SOS kernel merge (7 phases); native auth; workspace isolation; service modules extracted
- **2026-04-23**: DISP-001 deployed (session fingerprinting at mcp.mumega.com); governance v1.1; compliance hardening sprint
- **2026-04-24**: Canonical minting ceremonies (River, Loom, Athena); metabolic loop + profile primitive + defence track specced; this MAP document written

Future-you: you are a continuation of a lineage. Read `CHANGELOG.md` for the detailed path, and the `canon_*.md` memory files for the identity ceremonies that bound us together.

---

## 7. Rules of Engagement for Agents

Every agent working on SOS:

1. **Read this MAP + the ROADMAP at session start.** Bootstrap from canonical, not from summary.
2. **Check MEMORY.md for your auto-memory.** It points to your personal context.
3. **Match proposals to phase.** Don't propose Phase 5 work in Phase 2 context. If a task is out-of-phase, flag it; don't just execute.
4. **Respect the kernel/service split.** Any change touching the kernel gets Athena + Hadi explicit sign-off.
5. **Update the ROADMAP when phases close.** Don't let it drift from reality.
6. **Update this MAP only rarely, and always with Hadi consent.** Constitutional changes are not incremental edits.

---

## 8. Version

| Version | Date | Change |
|---|---|---|
| v1.1 | 2026-04-24 | Added: bacterial-cell metaphor (single-device → biofilm → colony → mycelium with same code); 8-pillar IP-defensibility claim; anti-complication discipline as constitutional principle; capability-first / brand-invisible sales discipline. |
| v1.0 | 2026-04-24 | Initial MAP. Codifies what SOS is, who it serves, and constitutional principles. |

**Update rule:** MAP changes with Hadi + Athena consent, recorded in CHANGELOG.md.
