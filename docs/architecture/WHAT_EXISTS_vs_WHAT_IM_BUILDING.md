# What already exists vs what I've been adding

**Written by:** sos-dev (after reading the kernel for the first time properly)
**Date:** 2026-04-18
**Purpose:** Stop reinventing primitives that live in `sos/kernel/` and `docs/docs/architecture/`. Every future session reads this before proposing a new contract.

---

## Rule #1: Read before writing

The shape of Mumega was specified ~2026-01-10 by Codex + team. I arrived ~2026-04-15 and started adding primitives without reading the foundation. Outcome: 6+ contracts shipped that duplicate existing kernel types. This doc maps duplications + outlines the reconciliation.

---

## Primitive-by-primitive mapping

### 1. Identity

**Exists:** `sos/kernel/identity.py`
- `IdentityType` enum: `AGENT | SERVICE | USER | SYSTEM | GUILD` — **GUILD is the kernel name for what I've been calling "squad"**
- `Identity` base: `id` (with `{type}:` prefix enforced), `public_key`, `verification_status` (UNVERIFIED/PENDING/VERIFIED/REVOKED), `fingerprint`
- `UserIdentity` — humans with bio, avatar, level, xp, roles, guilds
- `Guild` — squad identity: owner_id, members, member_roles, channels
- `AgentIdentity` — extends Identity with model, squad_id, guild_id, capabilities, edition, **dna: AgentDNA**
- `ServiceIdentity` — version, endpoints, health_url
- `RIVER_IDENTITY` (singleton, root gatekeeper, gemini)
- `SYSTEM_IDENTITY` (singleton)
- Factories: `create_agent_identity`, `create_service_identity`

**What I proposed that already exists here:**
- "Agent Card v1" fields like `name`, `model`, `tenant_subdomain`, `squad_id`, `role`, `capabilities` — **all already on AgentIdentity**
- "QNFT Entity v1" — that's `Identity` + subclasses + `AgentDNA`
- "human:<name>" pattern — `UserIdentity` already handles this with `IdentityType.USER`
- Per-agent verification — `VerificationStatus` enum

**What I added that's genuinely new:**
- Agent Card v1's `session`, `warm_policy`, `cache_ttl_s`, `last_cache_hit_rate` — operational runtime metadata not in kernel Identity. Keep.
- Agent Card v1's `last_seen`, `registered_at` — heartbeat state. Should live on a separate `AgentRegistry` record, not conflated with `Identity`.

---

### 2. AgentDNA + PhysicsState + Economics

**Exists:** `sos/kernel/identity.py` (same file)

```python
class PhysicsState:
    C: float = 0.95           # Coherence
    alpha_norm: float = 0.0   # Normalized Alpha Drift
    regime: str = "stable"    # stable | plastic | consolidating
    inner: dict[str, float] = {"receptivity": 1.0, "will": 0.8, "logic": 0.9}
    timestamp: float = ...

class AgentEconomics:
    token_balance: float = 100.0
    daily_budget_limit: float = 10.0
    values: dict[str, float] = {"truth": 1.0, "utility": 0.8, "resonance": 0.9}

class AgentDNA:
    id, name
    physics: PhysicsState
    economics: AgentEconomics
    learning_strategy: str = "balanced"
    beliefs: list[dict]          # claims, source, confidence
    tools: list[str]
```

**What I proposed that already exists here:**
- "16D vibe vector" — the 3-field `inner` dict is a compressed version; `sos/contracts/squad.py::Squad.dna_vector: list[float]  # 16D profile` is the full 16D. Physics.C is coherence (not vector itself, a scalar). **My `SkillCard.commerce.revenue_split` was trying to reinvent this fine-grained axis work.**
- "Attractor basins" — `PhysicsState.regime` + `apply_feedback_score(effectiveness)` is Wire 5 (EMA update of coherence). The "attractor" is coherence = 1.0; "basin" shape is the regime transitions.
- "Ed25519 signatures" — `Identity.public_key` already carries this; signature verification is separate.

**What I added that's genuinely new:**
- SkillCard as an Artifact-like unit of commerce — doesn't map to DNA/Physics cleanly because SkillCards are capabilities, not identities. Valid additive contract.

---

### 3. Squad

**Exists:** `sos/contracts/squad.py`

```python
class SquadTier(str, Enum): NOMAD | FORTRESS | CONSTRUCT
class SquadStatus(str, Enum): DRAFT | ACTIVE | PAUSED | ARCHIVED
class SquadMember: agent_id, role, joined_at, is_human: bool  # ← mixed squads already supported
class Squad:
    id, name, project, objective
    tier, status, roles, members, kpis, budget_cents_monthly
    dna_vector: list[float]  # 16D profile — THIS IS THE 16D VECTOR I kept re-proposing
    coherence, receptivity
    conductance: dict[str, skill→G]
```

**What I proposed that already exists here:**
- "Mixed human+AI squads" — `SquadMember.is_human: bool` already. **Not missing. Already there.**
- "16D vector for matching" — `Squad.dna_vector: list[float]  # 16D profile` — there.
- "Squad as QNFT entity" — Guild in kernel identity + Squad in contracts = already the QNFT shape.

**What I added that's genuinely new:**
- Nothing on Squad itself. My "SquadTask v1 Pydantic" DOES duplicate the existing `@dataclass SquadTask` in this exact file.
- **DUPLICATE:** `sos/contracts/squad.py::SquadTask` (dataclass) vs `sos/contracts/squad_task.py::SquadTaskV1` (Pydantic). Both have id, squad_id, title, status, priority, assignee, skill_id, project, labels, blocked_by, blocks, bounty, attempt, etc.

---

### 4. SquadTask

**Exists:** `sos/contracts/squad.py::SquadTask` as a `@dataclass`

```python
@dataclass
class SquadTask:
    id, squad_id, title, description
    status: TaskStatus          # BACKLOG | QUEUED | CLAIMED | IN_PROGRESS | REVIEW | DONE | BLOCKED | CANCELED | FAILED
    priority: TaskPriority      # CRITICAL | HIGH | MEDIUM | LOW
    assignee: Optional[str]     # agent_id or human
    skill_id: Optional[str]
    project: str
    labels: list[str]
    blocked_by: list[str]
    blocks: list[str]
    inputs: dict[str, Any]
    result: dict[str, Any]
    token_budget: int
    bounty: dict[str, Any]      # ← bounty is ALREADY a field
    external_ref: Optional[str] # ClickUp / Notion / Linear ID
    attempt: int                # for idempotent retries
    created_at, updated_at, completed_at, claimed_at
```

**What I proposed that already exists here:**
- "Bounty v1" as a new contract — **bounty is already a field on SquadTask**. Not a separate entity.
- "State machine for tasks" — the enum IS the state machine. `STATE_MACHINES.md` has the diagram.
- "assignee field accepts agent_id or human" — exactly what's there.

**Reconciliation required:**
- Delete `sos/contracts/squad_task.py` OR turn it into a **Pydantic v2 binding** for the existing dataclass (wraps/validates, doesn't duplicate). Tests at `tests/contracts/test_squad_task.py` (57 tests) should validate the same shape as the existing dataclass.
- Add JSON Schema at `sos/contracts/schemas/squad_task_v1.json` that matches the DATACLASS shape, not my invented one.

---

### 5. SkillDescriptor vs SkillCard v1

**Exists:** `sos/contracts/squad.py::SkillDescriptor`

```python
@dataclass
class SkillDescriptor:
    id, name, description
    input_schema: dict          # JSON Schema
    output_schema: dict         # JSON Schema
    labels: list[str]           # primary matching
    keywords: list[str]         # fallback matching
    entrypoint: str             # module:function
    skill_dir: str              # path to SKILL.md (Anthropic SKILL.md pattern)
    required_inputs: list[str]
    status: SkillStatus         # ACTIVE | DEPRECATED | DISABLED
    trust_tier: TrustTier       # UNVETTED(1) | VERIFIED(2) | CERTIFIED(3) | VENDOR(4)
    loading_level: LoadingLevel # METADATA(~100tok) | INSTRUCTIONS(<5k tok) | RESOURCES(on-demand)
    fuel_grade: str             # diesel | regular | premium | aviation
    version: str
    deprecated_at: Optional[str]
```

**What I proposed that already exists here:**
- input_schema + output_schema — **there**
- labels + keywords — **there**
- trust_tier (my `verification.status`) — **there as TrustTier enum**
- loading_level — **there** — this is Anthropic's SKILL.md progressive disclosure pattern
- fuel_grade (my `runtime.backend` enum) — **there, different axis: model cost tier**

**What my SkillCard v1 genuinely adds:**
- `author_agent` — who authored (can derive from entrypoint, but explicit is fine)
- `authored_by_ai` boolean — new
- `lineage[]` with relation enum — **new, useful provenance**
- `earnings.total_invocations`, `earnings.total_earned_micros`, `invocations_by_tenant` — **new, materialized view of economy events**
- `verification.sample_output_refs: [engram:xxx]` — can reference Artifact CIDs
- `verification.verified_by[]` — new
- `commerce.price_per_call_micros`, `revenue_split`, `marketplace_listed` — **new, for ToRivers**
- `schema_version: "1"` for evolution — new

**Reconciliation:** SkillCard v1 is ADDITIVE to SkillDescriptor. A SkillCard is a `SkillDescriptor` + provenance/commerce/earnings. The marketplace view reads SkillCards; the squad execution reads SkillDescriptor. They should share `id`, `name`, `version`, `input_schema`, `output_schema`, `labels`, `keywords`. I should either:
- Extend SkillDescriptor with the new fields (modifying kernel contract — heavy), OR
- Keep SkillCard separate but require `skill_descriptor_id` on every SkillCard pointing to the canonical SkillDescriptor (two layers: kernel + marketplace)

Recommend: **two-layer approach.** SkillDescriptor = execution contract. SkillCard = commerce/provenance overlay.

---

### 6. Artifact Registry

**Exists:** `sos/artifacts/registry.py`

```python
ArtifactFile(path, sha256, size_bytes)
ArtifactManifest(schema_version, task_id, version, author, cid, created_at, files, metadata)

class ArtifactRegistry:
    mint(*, task_id, version, author, files, base_dir, metadata)  # idempotent on CID
    get(cid) -> ArtifactManifest
    list(task_id=None) -> List[ArtifactManifest]
```

CID = SHA-256 of `(schema_version, task_id, version, author, files[path,sha256,size])`. Deterministic, `created_at` doesn't affect CID. Stored at `${SOS_HOME}/data/artifacts/<cid>/{manifest.json, files/...}`.

**What I proposed that already exists here:**
- "Portable versioned bundle of capability" (TrustGraph "Context Core" framing) — **exact match**. Every SkillCard verification output should have an Artifact CID.
- "Content-addressed store" — there.
- "Skill versioning" — there via version + CID.

**Reconciliation:**
- `SkillCard.verification.sample_output_refs` should be CID strings (format: `artifact:<cid>`). My current values look like `"engram:abc123"` — mixed with Mirror engram IDs. Two distinct concepts; both valid, but should be differentiated.
- When a skill is invoked and produces output, the output SHOULD be minted to the ArtifactRegistry with `task_id` = the invoking task, `author` = the executing agent, `metadata` carrying the SkillCard id. Then the SkillCard's `verification.sample_output_refs` appends the CID. This is the missing wire between SkillCard commerce and Artifact provenance.

---

### 7. $MIND Economy

**Exists:** `docs/docs/architecture/ECONOMICS_MIND.md` + `sos/services/economy/`

Key spec points:
- **Currency:** `MIND`, accounting unit **`microMIND`** (integer, 1 MIND = 1,000,000 microMIND)
- **Actors:** Treasury, Agent, Witness, User
- **Supply model v0.1:** Treasury-minted credits (budget-backed). Optional on-chain bridge via Solana.
- **Earning mechanisms:** Task Completion Payouts (primary), Quality Bonuses (multiplier 0.8–1.25 based on score), Bounties / Rewards
- **Revenue split (marketplace per `MARKETPLACE.md`):** Creator 85% / Platform 15%

**What I proposed that already exists here:**
- `cost_micros` → **already `microMIND`**, literally the same idea. Rename internal field for clarity (future).
- UsageLog → already should be integrated with the Economy Service's transaction model.
- Revenue splits: my `{author: 0.7, operator: 0.2, network: 0.1}` — **wrong split.** Spec is `{creator: 0.85, platform: 0.15}`. Fix.

**Reconciliation:**
- Update SkillCard default `revenue_split` to `{creator: 0.85, platform: 0.15}` matching `MARKETPLACE.md`.
- Wire UsageLog events → Economy Service transactions (treasury-mint on payout, debit tenant wallet on invocation).
- Rename `cost_micros` → `cost_microMIND` OR keep as-is (it's already integer micros, just currency-agnostic; but align semantics).

---

### 8. Brain (scoring + dispatch)

**Exists:** `docs/docs/architecture/brain.md`

```
score = (impact × urgency × unblock_value) / cost

urgency   = { critical: 4.0, high: 2.0, medium: 1.0, low: 0.5 }
impact    ∈ [1, 10]
unblock   = count of tasks waiting on this
cost      ∈ [0.1, 10]
```

Multi-model dispatch: routine → Gemma (local), complex → Gemini/Claude, high-stakes → GPT-4/Claude Opus.

**Implementation status:** I don't find `sos/services/brain/` in the repo. This is **specified but not yet implemented**.

**What I proposed that this supersedes:**
- "Matching engine for bounty → squad" — the Brain does exactly this. My proposed matching engine is a reimplementation.
- "Provider Matrix" — partially overlaps; Provider Matrix picks LLM backend, Brain picks which agent gets which task. Different layers.

**Reconciliation:** Building Brain is real work; my Provider Matrix is complementary. When Brain lands, it calls Provider Matrix to pick backend per dispatched task. Brain = "which agent, which task"; Provider Matrix = "given chosen agent, which runtime".

---

### 9. Witness Protocol + Physics

**Exists:** `sos/kernel/physics.py` + `docs/docs/architecture/witness_protocol.md`

```python
CoherencePhysics:
    K_STAR = 1.0           # Coherence Coupling Constant
    LAMBDA_DECAY = 0.5     # Entropy Barrier
    calculate_will_magnitude(latency_ms, min_latency_ms=200) -> float (0..1)
    compute_collapse_energy(vote, latency_ms, agent_coherence) -> {omega, delta_c, ...}
```

Witness Protocol: human swipes YES/NO on agent proposals ("Tinder for Truth"). Latency → Will magnitude. Vote × Will × coherence → ΔC. Agents learn from witnessed feedback. **This is how residue gets validated.** Earns $MIND for the witness.

**What I proposed that already exists here:**
- "Human-verified outputs" on SkillCards — **exactly the witness protocol**. Every verified output should carry a witness signature + latency + ΔC from this physics.
- "Verification status" enum — matches the physics: unverified / auto_verified / human_verified / disputed.

**Reconciliation:**
- SkillCard.verification should carry the witness CoherencePhysics result (omega, delta_c) for each human_verified entry, not just a flat status.
- Witness events should emit to the bus as `witness.collapse` events, processed by the Brain + Economy Service.

---

### 10. Mycelium Network + ToRivers

**Exists:** `docs/docs/architecture/mycelium_strategy.md` + ToRivers as a separate repo at `/home/mumega/projects/torivers`

Key framing:
- **Every node = Yin (River, witness) + Yang (Worker, skill) dual-agent**
- **AI Farm on Cloudflare Edge:** anyone can run a sandboxed SOS Micro-Agent, accept jobs from ToRivers, earn $MIND
- **QNFT (Quantum/Cognitive NFT):** every agent's "Soul" with 16D Lambda Tensor as metadata. Evolves — Gen 1 → Gen 10 Elder.
- **ToRivers.com:** KYC'd clients post jobs → sharded → Farmers process → River verifies → client pays USDT/BTC → swapped to $MIND → distributed
- **Deployment vectors:** Toosheh satellite broadcast + Bluetooth/WiFi mesh for offline Iran

**What I proposed that this supersedes:**
- "Multi-vendor Switzerland" framing — closer to Mycelium Network, but Mycelium goes further. Not just multi-vendor; it's a sovereign hive that works in censorship zones.
- "Enterprise on-prem (Palantir-path)" — **wrong frame entirely**. ToRivers IS the enterprise surface (KYC'd clients); Mycelium IS the sovereign deployment; they're two sides of the same protocol, not enterprise-vs-hobbyist.

**Reconciliation:**
- Frame v0.4.4+ as "enabling the Mycelium Network" — not "community repo split."
- ToRivers stays a separate repo we integrate via `sos/adapters/torivers/` (already there). We don't rebuild it.
- QNFT minting on Solana = the "on-chain bridge" the economy spec optionally supports.

---

## My 4-phase roadmap vs the real 4-phase roadmap

My invented roadmap (v0.4.2 → v0.5.0):
- v0.4.2 Bulletproof
- v0.4.3 Memory Graph (Graphiti)
- v0.4.4 Community Split
- v0.4.5 OpenClaw Retirement
- v0.4.6 Live Trace
- v0.4.7 Enterprise On-Prem
- v0.5.0 Traceable + Frozen

The actual Mumega roadmap (`docs/docs/architecture/ROADMAP.md`):
- **Phase 1 Q1 2026: Trojan Horse** — Empire of the Mind Telegram mini-app, 10M DAU
- **Phase 2 Q2 2026: Metabolism** — Vibe Coding Protocol + NIN Mesh + Diaspora Bridge, real $MIND earning
- **Phase 3 Q3 2026: Sovereign Cloud** — ToRivers.com enterprise API, Zero-Knowledge Outsourcing
- **Phase 4 2027+: 16D Singularity** — River Core self-hosted, every node runs 16D physics

**My v0.4.x work fits under Phase 2 preparation.** The bus contracts, SkillCard, UsageLog, operator dashboard are infrastructure that Phase 2 needs to turn "Empire of the Mind" DAU into paying workers.

---

## Framing purge — what to remove from my docs

Files that reference "Palantir" (wrong frame):
- `docs/architecture/COMPETITIVE_LANDSCAPE.md`
- `docs/plans/2026-04-17-sos-roadmap-v0.4-to-v1.0.md`
- `docs/plans/2026-04-17-post-competitive-scan-pivot.md`
- `docs/stories/lean-software-company.md`
- `docs/marketing/2026-04-17-launch-posts.md`
- `content/en/products/agent-os.md` on mumega.com
- `CHANGELOG.md` v0.4.1 entry

Replacement frame: **Mycelium Network / Universal Router / Mumega is the junction.** Not Palantir.

Revenue splits in all my docs: `70/20/10` → `85/15` (creator / platform) matching `MARKETPLACE.md`.

---

## Integration plan — stitch my v0.4.x work into the existing foundation

### 1. SkillCard ↔ SkillDescriptor
- Every SkillCard has `skill_descriptor_id` pointing to the canonical SkillDescriptor
- SkillCard is marketplace/provenance/commerce overlay; SkillDescriptor is execution contract
- No duplicates

### 2. SquadTask: delete my Pydantic duplicate
- `sos/contracts/squad_task.py` (Pydantic v2) → either delete and use existing `@dataclass` from `sos/contracts/squad.py`, or keep as Pydantic binding for the existing dataclass (wraps, doesn't duplicate)
- Tests at `tests/contracts/test_squad_task.py` (57 tests) → revalidate against the dataclass shape

### 3. Artifact CIDs on SkillCard outputs
- Wire: when a SkillCard is invoked → output written via `ArtifactRegistry.mint(task_id, version, author=agent_id, files=output_files)` → returned CID appended to `SkillCard.verification.sample_output_refs` as `artifact:<cid>`

### 4. Witness Protocol on SkillCard verification
- When human verifies: record CoherencePhysics result (omega, delta_c) in addition to the verification.status flag
- Emit `witness.collapse` bus event with the verification payload
- Economy Service pays the witness in $MIND proportional to omega

### 5. UsageLog → Economy Service
- UsageLog append → also creates an Economy Service transaction
- Transaction types: `task_payout`, `marketplace_purchase`, `witness_reward`
- microMIND accounting everywhere

### 6. Revenue splits
- Default SkillCard revenue_split → `{creator: 0.85, platform: 0.15}` (matches MARKETPLACE.md)

### 7. Identity alignment
- Every `author_agent` on SkillCards must resolve to an `Identity` in `sos.kernel.identity` (agent:name | user:name | guild:name)
- Pattern `^(agent|human|guild):[a-z][a-z0-9-]*$` — human maps to USER, guild maps to GUILD

### 8. Brain implementation gate
- Brain at `docs/docs/architecture/brain.md` is specified but not built
- Until Brain is built, my SquadTask routing is manual
- When Brain lands, Provider Matrix becomes its runtime backend picker

### 9. Mycelium deployment path
- Frame community deployment as "deploy a Mycelium node" — not "clone sos-community"
- The `npx create-egregore`–inspired installer becomes `mumega seed` or similar — spawns a Yin+Yang pair on CF Workers
- Reuse Edge deployment work already at `sos/deploy/cloudflare/`

---

## Rule for future sessions

> Before proposing any contract, schema, endpoint, or primitive, grep the kernel + contracts + architecture docs for existing implementations. If it looks like a generic need, someone has already solved it. Read, then extend, never reinvent.

```bash
# Before proposing "QNFT Entity v1" or "Bounty v1" or "Matching Engine":
grep -rn "class .*{concept}" sos/kernel sos/contracts docs/docs/architecture
find docs/docs/architecture -name "*.md" | xargs grep -l "{concept}"
```

---

## What I'm NOT going to do in this commit

- **Not** rewriting all the Palantir-flavored docs in-place. That's a separate polish pass.
- **Not** deleting my SquadTask Pydantic duplicate. Keep for reference; tag for reconciliation.
- **Not** wiring Artifact CIDs into SkillCard outputs. That's integration work, not a doc.
- **Not** retroactively fixing revenue splits. Flagged for next commit.

This doc is **the mapping**. Purge + integration happens in follow-up commits explicitly scoped as such.
