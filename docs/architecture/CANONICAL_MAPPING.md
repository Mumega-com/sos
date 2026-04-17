# Canonical Mapping

One source of truth per concept.

## SquadTask
- **Canonical:** `sos/contracts/squad.py::SquadTask` (@dataclass)
- **Pydantic binding:** `sos/contracts/squad_task.py::SquadTaskV1`
- **JSON Schema:** `sos/contracts/schemas/squad_task_v1.json`
- Binding wraps dataclass; never define fields separately.
