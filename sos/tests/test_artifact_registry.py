import os
import shutil
import tempfile
import unittest
from pathlib import Path

from sos.artifacts.registry import ArtifactRegistry


class TestArtifactRegistry(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["SOS_HOME"] = self.temp_dir

        self.workspace = Path(self.temp_dir) / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        os.environ.pop("SOS_HOME", None)

    def test_mint_copies_files_and_is_deterministic(self):
        (self.workspace / "foo.txt").write_text("hello", encoding="utf-8")
        (self.workspace / "sub").mkdir(parents=True, exist_ok=True)
        (self.workspace / "sub" / "bar.txt").write_text("world", encoding="utf-8")

        registry = ArtifactRegistry()
        manifest = registry.mint(
            task_id="task_001",
            version="1.0.0",
            author="agent:codex",
            files=[self.workspace / "foo.txt", self.workspace / "sub" / "bar.txt"],
            base_dir=self.workspace,
        )

        self.assertEqual(manifest.task_id, "task_001")
        self.assertEqual(len(manifest.cid), 64)

        artifact_dir = registry.artifact_dir(manifest.cid)
        self.assertTrue((artifact_dir / "manifest.json").exists())
        self.assertTrue((artifact_dir / "files" / "foo.txt").exists())
        self.assertTrue((artifact_dir / "files" / "sub" / "bar.txt").exists())

        # Idempotent: re-mint returns same CID
        manifest2 = registry.mint(
            task_id="task_001",
            version="1.0.0",
            author="agent:codex",
            files=[self.workspace / "foo.txt", self.workspace / "sub" / "bar.txt"],
            base_dir=self.workspace,
        )
        self.assertEqual(manifest2.cid, manifest.cid)

    def test_mint_changes_cid_when_content_changes(self):
        path = self.workspace / "a.txt"
        path.write_text("one", encoding="utf-8")

        registry = ArtifactRegistry()
        m1 = registry.mint(
            task_id="task_002",
            version="1.0.0",
            author="agent:codex",
            files=[path],
            base_dir=self.workspace,
        )

        path.write_text("two", encoding="utf-8")
        m2 = registry.mint(
            task_id="task_002",
            version="1.0.0",
            author="agent:codex",
            files=[path],
            base_dir=self.workspace,
        )

        self.assertNotEqual(m1.cid, m2.cid)


if __name__ == "__main__":
    unittest.main()

