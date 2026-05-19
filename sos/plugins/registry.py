from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sos.artifacts.registry import ArtifactManifest, ArtifactRegistry
from sos.plugins.manifest import PluginManifest, verify_plugin_manifest_signature


@dataclass(frozen=True)
class LoadedPlugin:
    cid: str
    manifest: PluginManifest
    artifact: ArtifactManifest
    files_dir: Path


class PluginRegistry:
    """
    Plugin registry backed by the Artifact Registry.

    Convention: plugin manifests live at `files/plugin.json` within an artifact CID.
    """

    def __init__(self, artifacts: Optional[ArtifactRegistry] = None):
        self._artifacts = artifacts or ArtifactRegistry()

    def load(
        self,
        cid: str,
        *,
        verify_key: Optional[bytes] = None,
        manifest_relpath: str = "plugin.json",
    ) -> LoadedPlugin:
        artifact = self._artifacts.get(cid)
        files_dir = self._artifacts.artifact_dir(cid) / "files"
        manifest_path = files_dir / manifest_relpath
        if not manifest_path.exists():
            raise FileNotFoundError(f"Plugin manifest not found in artifact {cid}: {manifest_relpath}")

        manifest = PluginManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        if verify_key is not None:
            ok, reason = verify_plugin_manifest_signature(manifest, verify_key)
            if not ok:
                raise ValueError(reason)

        return LoadedPlugin(
            cid=cid,
            manifest=manifest,
            artifact=artifact,
            files_dir=files_dir,
        )

