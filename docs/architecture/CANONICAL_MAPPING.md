# Canonical Mapping

One source of truth per concept.

## SquadTask
- **Canonical:** `sos/contracts/squad.py::SquadTask` (@dataclass)
- **Pydantic binding:** `sos/contracts/squad_task.py::SquadTaskV1`
- **JSON Schema:** `sos/contracts/schemas/squad_task_v1.json`
- Binding wraps dataclass; never define fields separately.

## Artifact identifiers
- **Canonical artifact store:** `sos/artifacts/registry.py::ArtifactRegistry`
- **CID format:** `artifact:<sha256-hex-64>`
- **Legacy engram identifiers** (`engram:<slug>`) remain accepted for backward compat but new verification outputs should use `artifact:` CIDs after calling `ArtifactRegistry.mint(...)`
- **Used by:** `SkillCard.verification.sample_output_refs`, `SkillCard.verification.primary_artifact_cid`, future `WitnessEvent.output_cid`

## Bus events

- **Canonical event-type strings:** dot-separated (`task.created`, `skill.executed`, etc.) per `sos/contracts/squad.py::SQUAD_EVENTS`
- **Pydantic bindings:** `sos/contracts/messages.py::<Type>Message`
- **JSON Schema:** `sos/contracts/schemas/messages/<type>_v1.json`
- **Enforcement list:** `sos/services/bus/enforcement.py::_V1_TYPES`
- Rule: add to SQUAD_EVENTS first, then create v1 binding, then update enforcement.

### Type table

| Event type | Pydantic class | JSON Schema file | In SQUAD_EVENTS |
|---|---|---|---|
| `announce` | `AnnounceMessage` | `announce_v1.json` | no (bus protocol) |
| `send` | `SendMessage` | `send_v1.json` | no (bus protocol) |
| `wake` | `WakeMessage` | `wake_v1.json` | no (bus protocol) |
| `ask` | `AskMessage` | `ask_v1.json` | no (bus protocol) |
| `agent_joined` | `AgentJoinedMessage` | `agent_joined_v1.json` | no (bus protocol) |
| `task.created` | `TaskCreatedMessage` | `task.created_v1.json` | yes |
| `task.claimed` | `TaskClaimedMessage` | `task.claimed_v1.json` | yes |
| `task.completed` | `TaskCompletedMessage` | `task.completed_v1.json` | yes |
| `task.routed` | `TaskRoutedMessage` | `task.routed_v1.json` | yes |
| `task.failed` | `TaskFailedMessage` | `task.failed_v1.json` | yes |
| `skill.executed` | `SkillExecutedMessage` | `skill.executed_v1.json` | yes |

### Renamed in island #10 (2026-04-18)

Before: v1 bus types used underscore names (`task_created`, `task_claimed`, `task_completed`).
After: dot-separated everywhere; kernel `SQUAD_EVENTS` wins.

## SkillCard and SkillDescriptor

- **Canonical execution contract:** `sos/contracts/squad.py::SkillDescriptor` — id, input/output schemas, entrypoint, trust_tier, loading_level, fuel_grade, version. This is what the squad service + Brain use to invoke.
- **Provenance + commerce overlay:** `sos/contracts/skill_card.py::SkillCard` — author_agent, lineage, earnings, verification, commerce terms, marketplace listing. References SkillDescriptor by `skill_descriptor_id`.
- **JSON Schema:** `sos/contracts/schemas/skill_card_v1.json`
- **Rule:** fields describing HOW the skill runs belong on SkillDescriptor. Fields describing WHO authored / WHAT it earned / commerce terms belong on SkillCard. Never duplicate.
- **Marketplace reads SkillCard + resolves SkillDescriptor.** Squad service reads SkillDescriptor directly.
- **input_schema / output_schema on SkillCard** are optional echo fields for display only; source of truth is the referenced SkillDescriptor.

_Added in island #2 — 2026-04-18-coherence-plus-us-market.md_

## AgentCard and AgentIdentity
- **Canonical identity + soul:** `sos/kernel/identity.py::AgentIdentity` + `AgentDNA` (physics, economics, learning_strategy, beliefs, tools). This is WHO the agent is.
- **Runtime operational view:** `sos/contracts/agent_card.py::AgentCard` — what the agent's runtime looks like NOW (session, pid, host, cache state, heartbeat, warm_policy). References AgentIdentity by `identity_id`.
- **Types enum expanded:** tmux, openclaw, remote, webhook, service, **hermes**, **codex**, **cma**, **human** — covers the multi-vendor substrate + human squad members per coherence plan.
- **Rule:** soul fields (public_key, dna, verification_status, capabilities) live on AgentIdentity. Runtime operational fields (session, pid, cache) live on AgentCard. `name`, `model` are echoed on AgentCard for display; source of truth is AgentIdentity.

_Added in island #3 — 2026-04-18-coherence-plus-us-market.md_
