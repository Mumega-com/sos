# Section 6 — Plugin Manifest & Forkability

**Scope:** how GAF (and every future customer-knight vertical) plugs into the SOS microkernel. Specifies the `plugin.yaml` contract, isolation boundaries, and fork procedure.

Context: SOS completed its microkernel refactor last week (SOV-001 through SOV-005). This section formalizes the plugin contract so GAF becomes the first reference implementation and every next vertical inherits the same shape.

## 6.1 Why plugins

Every knight serves a customer business. Every customer business has domain logic that doesn't belong in the kernel: SR&ED eligibility rules (GAF), RECO/TRESA showing agreements (AgentLink), dental practice management (DentalNearYou), real estate listing matching (TROP).

Kernel stays small and stable. Domain logic lives in plugins. Plugins plug in through a fixed contract.

Forkability comes free: `fork gaf → gaf-us` changes the eligibility rules + filing partners; keeps the kernel, the substrate, the knight protocol.

## 6.2 Directory layout

```
SOS/
├── kernel/                        # Stable substrate (no plugin code here)
│   ├── auth/
│   ├── bus/
│   ├── memory/                    # Mirror engrams
│   ├── role-registry/             # Section 1 primitives
│   ├── schema/
│   └── events/
├── services/                      # Core services, plugin-agnostic
│   ├── mirror/
│   ├── squad/
│   ├── engine/
│   ├── dispatcher/
│   └── inkwell/
└── plugins/
    ├── gaf/                       # First reference plugin
    │   ├── plugin.yaml            # Manifest (see 6.3)
    │   ├── migrations/            # Plugin-owned DB migrations
    │   ├── routes/                # Plugin-specific HTTP routes
    │   ├── agents/                # Knight + worker squad definitions
    │   ├── inkwell/               # Plugin project-tier pages
    │   ├── adapters/              # QBO, GitHub, payroll, etc.
    │   └── tests/
    ├── agentlink/                 # Second plugin (post-Matt-signature)
    ├── dnu/                       # DentalNearYou
    └── trop/                      # Realm of Patterns
```

## 6.3 `plugin.yaml` contract

Every plugin declares:

```yaml
# plugins/gaf/plugin.yaml
name: gaf
version: 1.0.0
display_name: Grant & Funding
owner: hadi@digid.ca
knight:
  default_name: kaveh
  model_tier: sonnet-4-6
  session_strategy: stateless
  qnft_seed_descriptor_file: ./qnft-descriptor.md

db_namespace: gaf  # prefix for tables: gaf_cases, gaf_evidence, gaf_binders
shared_tables:     # tables this plugin reads from kernel/services
  - kernel.roles
  - kernel.role_assignments
  - squad.customers
  - squad.partners
  - squad.opportunities
  - squad.commissions
  - mirror.engrams
  - inkwell.pages

bus_channels:
  emits:
    - project:gaf:*
    - case:*:lifecycle
    - binder:*:locked
    - binder:*:submitted
  listens:
    - project:gaf:*
    - bus:broadcast
    - coordinator:loom:*

roles:              # Plugin-specific roles (registered in kernel role-registry)
  - originator      # Accountant who refers customers
  - specialist      # SR&ED practitioner partner
  - developer       # Customer's tech lead (for Champion signoff)
  - customer-admin  # Customer's workspace admin

inkwell_tiers:
  project: gaf/{customer-slug}/*
  squad:   gaf/squad-kb/*
  public:  gaf/public/*

adapters:
  - github
  - quickbooks
  - slack
  - payroll (stubbed: wagepoint, adp, ceridian)
  - manual-upload

compliance_profile:
  data_residency: canada
  pii_regime: pipeda
  retention_years: 6             # CRA requirement
  audit_defensibility: cra       # Implies Section 2 compliance fixes
  independence_required: true    # Section 2C human attestation

discord:
  channel_pattern: "#gaf-{customer-slug}"
  entity_channel_pattern: "#entity-{partner-slug}"

capabilities:     # What operations this plugin exposes via its knight
  - scan_eligibility
  - ingest_evidence
  - synthesize_narrative
  - verify_dossier  # requires practitioner_partner role
  - lock_binder
  - handoff_for_filing
  - track_claim_status
  - record_commission

fork_safe: true  # Whether this plugin can be forked for another region/vertical
fork_variants:
  - us-sred  # hypothetical: US R&D credit fork
  - uk-rdec  # hypothetical: UK equivalent
```

**Validation:** kernel reads `plugin.yaml` on plugin registration. Rejects invalid manifests. Schema lives at `kernel/schema/plugin-manifest.schema.json`.

## 6.4 Isolation boundaries (what a plugin CAN'T do)

- **Cannot modify kernel code** — plugins live in `plugins/`; kernel stays read-only from plugin perspective
- **Cannot access another plugin's DB namespace** — RLS policy enforces `gaf_*` tables invisible to `agentlink_*` code paths
- **Cannot mint roles outside its declared set** — role-registry rejects unknown role names from plugin
- **Cannot emit bus events outside declared channels** — bus-gateway rejects unscoped emissions
- **Cannot read engrams outside its scope** — Mirror recall gate (Section 1C) enforces plugin-workspace-entity scoping
- **Cannot write Inkwell pages outside its declared tiers** — Inkwell RBAC middleware (Section 1B) enforces
- **Cannot bypass compliance_profile** — if `independence_required: true`, knight cannot auto-verify; human signoff enforced at hand-off gate

## 6.5 What a plugin GETS from the kernel

For free, on registration:
- Bus channel allocation (gets `project:{name}:*` auto-subscribed)
- Role-registry access (scoped to its declared roles)
- Mirror engram namespace (entity_id partitioned)
- Inkwell tier namespace (gaf/ subtree)
- Supabase schema namespace (gaf_*)
- Knight mint capability (calls `mint-knight.py` with plugin-scoped args)
- Chat primitive (Supabase Realtime channel per case)
- Commission ledger (Stripe Connect integration already wired)
- Consent management (PIPEDA flow)
- Observability (metrics auto-roll-up to `/admin/plugins/{name}`)
- Canonical countersign workflow (if operational knight earns it per Section 1E)

## 6.6 Fork procedure

**To fork GAF for a new region/vertical:**

```bash
cd SOS/plugins/
cp -r gaf/ gaf-us/
cd gaf-us/
# Edit plugin.yaml: name=gaf-us, display_name="Grant & Funding (US)",
#                   compliance_profile.data_residency=us,
#                   compliance_profile.pii_regime=ccpa,
#                   adapters update for IRS instead of CRA
# Rewrite routes/eligibility.ts with US R&D credit rules
# Swap adapters for US-specific data sources (ADP US, etc.)
# Rewrite routes/filing.ts for IRS hand-off instead of CRA
# Update inkwell/ pages with US-specific content
# Update knight's qnft-descriptor.md with new cause (same shape, different soil)
make register-plugin gaf-us
```

Then mint a new knight:

```bash
python3 scripts/mint-knight.py \
  --plugin gaf-us \
  --knight-name <new-name> \
  --customer-slug <first-us-customer> \
  ...
```

The kernel handles the rest. Fork cost: ~1 week of domain-logic work; zero substrate work.

## 6.7 First non-GAF plugin mint (acid test)

**Target:** AgentLink, once Matt signs Option 2.

Steps:
1. Create `plugins/agentlink/` with manifest
2. Declare roles: `realtor`, `brokerage-admin`, `showing-buyer`, `showing-seller`
3. Declare adapters: `reco-api`, `tresa-forms`, `mls-stub`
4. Declare compliance profile: `data_residency: canada`, `pii_regime: pipeda`, `retention_years: 7`, `audit_defensibility: reco`
5. Mint knight (name TBD — Matt's call, but structurally same as Kaveh)
6. Provision Discord channel `#agentlink-phase1` (Century 21 Barrie pilot)
7. Run first showing end-to-end through the plugin's pipeline
8. Confirm isolation: AgentLink cannot see GAF's evidence, Kaveh cannot see AgentLink's cases

**Success criteria:** AgentLink runs on the same substrate as GAF, different domain logic, zero kernel changes required.

## 6.8 Kernel versioning & plugin compatibility

- Kernel semver: `kernel-1.0.0`
- Plugins declare `kernel_min_version` in manifest
- Breaking kernel changes bump major version; plugins must re-certify
- Kernel auto-upgrades are only patch versions (bug fixes); minor/major requires plugin acknowledgment

## 6.9 Open questions

1. Should plugin migrations run in plugin-private schemas or shared `public` with prefixes? (Leaning private schemas for stronger isolation.)
2. How do plugins share a customer? (A dental practice referred for SR&ED enters both `dnu` and `gaf` plugins. Need a `cross_plugin_customer_links` primitive in the kernel.)
3. Plugin marketplace v2 — can third parties write plugins against our kernel? Governance implications.
4. Fork lineage tracking — if `gaf-us` forks from `gaf`, should we maintain a `forked_from` chain for upstream patch propagation?

## 6.10 Dependencies

Depends on: Section 1 (role registry, engram tiers, Inkwell RBAC) — plugins can't isolate without these. Compliance profile enforcement depends on Section 2 fixes (Merkle lock, source timestamps, human attestation) being implemented in the kernel-or-library layer so plugins inherit correctness automatically.

**Owner:** Loom (manifest spec + fork procedure), Kasra (kernel enforcement + plugin loader), Athena (gate on first non-GAF plugin).

**Estimate:** 3-4 days to ship the manifest contract + loader; acid test with AgentLink is a separate track tied to Matt's signature.
