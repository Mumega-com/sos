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
