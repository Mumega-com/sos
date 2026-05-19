# SOS Artifact Registry (Draft)

Status: Draft v0.1  
Owner: Codex  
Last Updated: 2026-01-10

## Purpose
Provide a local-first, content-addressed registry for task outputs (“artifacts”) so SOS can:
- verify proof-of-work outputs by CID
- support safe rollback (“time travel”)
- enable a future plugin marketplace grounded in immutable bundles

This implements the “Artifact Registry” suggestion in `SOS/docs/SUGGESTIONS.md`.

## Storage Layout
Artifacts are stored under `${SOS_HOME}/data/artifacts/<cid>/`:
- `manifest.json`
- `files/<relative paths...>`

`SOS_HOME` defaults to `~/.sos` (see `sos/kernel/config.py`).

## CID
CID is a deterministic SHA-256 hex digest computed over:
- `schema_version`, `task_id`, `version`, `author`
- `files[]` with stable ordering by `path`, including `sha256` + `size_bytes`

`created_at` does **not** affect CID.

## API (Python)

### Mint an artifact
```python
from pathlib import Path
from sos.artifacts import ArtifactRegistry

registry = ArtifactRegistry()
manifest = registry.mint(
    task_id="task_001",
    version="1.0.0",
    author="agent:kasra",
    files=[Path("dist/plugin.py"), Path("dist/README.md")],
    base_dir=Path("dist"),
    metadata={"kind": "plugin"},
)
print(manifest.cid)
```

### Retrieve an artifact
```python
manifest = registry.get(cid)
print(manifest.files)
```

### List artifacts
```python
all_artifacts = registry.list()
task_artifacts = registry.list(task_id="task_001")
```

## Notes / Next Steps
- Add JSON Schema validation for manifests once River finalizes the “Law of Creation”.
- Add optional Ed25519 signing for manifests (author signature).
- Add a “rollback/deploy” helper that checks out a prior CID safely.

