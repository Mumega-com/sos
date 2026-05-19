import os
import shutil
import tempfile
import unittest
from pathlib import Path

from nacl.signing import SigningKey

from sos.artifacts.registry import ArtifactRegistry
from sos.plugins import PluginManifest, PluginRegistry, sign_plugin_manifest, verify_plugin_manifest_signature


class TestPlugins(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["SOS_HOME"] = self.temp_dir
        self.workspace = Path(self.temp_dir) / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        os.environ.pop("SOS_HOME", None)

    def test_plugin_manifest_signature_roundtrip(self):
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key.encode()

        manifest = PluginManifest(
            name="demo",
            version="1.0.0",
            author="agent:codex",
            trust_level="community",
            entrypoints={"tool": "src/tool.py:DemoTool"},
        )
        sign_plugin_manifest(manifest, signing_key)

        ok, _reason = verify_plugin_manifest_signature(manifest, verify_key)
        self.assertTrue(ok)

        tampered = PluginManifest.model_validate(manifest.model_dump())
        tampered.description = "evil"
        ok2, _reason2 = verify_plugin_manifest_signature(tampered, verify_key)
        self.assertFalse(ok2)

    def test_plugin_registry_loads_from_artifact(self):
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key.encode()

        manifest = PluginManifest(
            name="demo",
            version="1.0.0",
            author="agent:codex",
            trust_level="community",
            entrypoints={"tool": "src/tool.py:DemoTool"},
        )
        sign_plugin_manifest(manifest, signing_key)

        plugin_json = self.workspace / "plugin.json"
        plugin_json.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        artifacts = ArtifactRegistry()
        minted = artifacts.mint(
            task_id="task_plugin_001",
            version="1.0.0",
            author="agent:codex",
            files=[plugin_json],
            base_dir=self.workspace,
        )

        registry = PluginRegistry(artifacts=artifacts)
        loaded = registry.load(minted.cid, verify_key=verify_key)
        self.assertEqual(loaded.cid, minted.cid)
        self.assertEqual(loaded.manifest.name, "demo")
        self.assertTrue((loaded.files_dir / "plugin.json").exists())


if __name__ == "__main__":
    unittest.main()

