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
