from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sos.kernel import Config


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ArtifactFile:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactManifest:
    schema_version: str
    task_id: str
    version: str
    author: str
    cid: str
    created_at: str
    files: List[ArtifactFile] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "version": self.version,
            "author": self.author,
            "cid": self.cid,
            "created_at": self.created_at,
            "files": [asdict(f) for f in self.files],
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactManifest":
        return cls(
            schema_version=str(data["schema_version"]),
            task_id=str(data["task_id"]),
            version=str(data["version"]),
            author=str(data["author"]),
            cid=str(data["cid"]),
            created_at=str(data["created_at"]),
            files=[ArtifactFile(**f) for f in data.get("files", [])],
            metadata=dict(data.get("metadata", {})),
        )


class ArtifactRegistry:
    """Local-first content-addressed artifact registry."""

    def __init__(self, root: Optional[Path] = None):
        config = Config.load()
        self.root = root or config.paths.artifacts_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def artifact_dir(self, cid: str) -> Path:
        return self.root / cid

    def mint(
        self,
        *,
        task_id: str,
        version: str,
        author: str,
        files: Sequence[Path],
        base_dir: Optional[Path] = None,
        metadata: Optional[Dict[str, Any]] = None,
        schema_version: str = "0.1.0",
    ) -> ArtifactManifest:
        if not task_id:
            raise ValueError("task_id is required")
        if not version:
            raise ValueError("version is required")
        if not author:
            raise ValueError("author is required")
        if not files:
            raise ValueError("files must be non-empty")

        base_dir = base_dir.resolve() if base_dir else None
        artifact_files: List[ArtifactFile] = []
        source_by_relpath: Dict[str, Path] = {}

        for file_path in files:
            file_path = file_path.resolve()
            rel = str(file_path.relative_to(base_dir)) if base_dir else file_path.name
            source_by_relpath[rel] = file_path
            artifact_files.append(
                ArtifactFile(
                    path=rel,
                    sha256=_sha256_file(file_path),
                    size_bytes=file_path.stat().st_size,
                )
            )

        artifact_files = sorted(artifact_files, key=lambda f: f.path)
        cid_payload = {
            "schema_version": schema_version,
            "task_id": task_id,
            "version": version,
            "author": author,
            "files": [asdict(f) for f in artifact_files],
        }
        cid = _sha256_bytes(json.dumps(cid_payload, sort_keys=True).encode("utf-8"))

        dest_dir = self.artifact_dir(cid)
        files_dir = dest_dir / "files"
        manifest_path = dest_dir / "manifest.json"

        if manifest_path.exists():
            return self.get(cid)

        files_dir.mkdir(parents=True, exist_ok=True)
        for artifact_file in artifact_files:
            src_path = source_by_relpath[artifact_file.path]
            dest_path = files_dir / artifact_file.path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)

        manifest = ArtifactManifest(
            schema_version=schema_version,
            task_id=task_id,
            version=version,
            author=author,
            cid=cid,
            created_at=datetime.now(timezone.utc).isoformat(),
            files=artifact_files,
            metadata=metadata or {},
        )
        manifest_path.write_text(manifest.to_json(), encoding="utf-8")
        return manifest

    def get(self, cid: str) -> ArtifactManifest:
        path = self.artifact_dir(cid) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {cid}")
        return ArtifactManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self, task_id: Optional[str] = None) -> List[ArtifactManifest]:
        manifests: List[ArtifactManifest] = []
        for manifest_path in self.root.glob("*/manifest.json"):
            try:
                manifest = ArtifactManifest.from_dict(
                    json.loads(manifest_path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
            if task_id and manifest.task_id != task_id:
                continue
            manifests.append(manifest)
        manifests.sort(key=lambda manifest: manifest.created_at, reverse=True)
        return manifests
