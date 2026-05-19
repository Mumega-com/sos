import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from sos.services.tools.app import app


class TestToolsExecutionFlag(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.other_dir = tempfile.mkdtemp()

        self._prev_home = os.environ.get("SOS_HOME")
        self._prev_exec = os.environ.get("SOS_TOOLS_EXECUTION_ENABLED")
        self._prev_allowed = os.environ.get("SOS_TOOL_ALLOWED_ROOTS")
        self._prev_base = os.environ.get("SOS_TOOL_BASE_DIR")

        os.environ["SOS_HOME"] = self.temp_dir
        os.environ["SOS_TOOLS_EXECUTION_ENABLED"] = "1"
        os.environ.pop("SOS_TOOL_ALLOWED_ROOTS", None)
        os.environ.pop("SOS_TOOL_BASE_DIR", None)

        self.allowed_file = Path(self.temp_dir) / "allowed.txt"
        self.allowed_file.write_text("hello", encoding="utf-8")

        self.blocked_file = Path(self.other_dir) / "blocked.txt"
        self.blocked_file.write_text("nope", encoding="utf-8")

        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        shutil.rmtree(self.other_dir)

        if self._prev_home is None:
            os.environ.pop("SOS_HOME", None)
        else:
            os.environ["SOS_HOME"] = self._prev_home

        if self._prev_exec is None:
            os.environ.pop("SOS_TOOLS_EXECUTION_ENABLED", None)
        else:
            os.environ["SOS_TOOLS_EXECUTION_ENABLED"] = self._prev_exec

        if self._prev_allowed is None:
            os.environ.pop("SOS_TOOL_ALLOWED_ROOTS", None)
        else:
            os.environ["SOS_TOOL_ALLOWED_ROOTS"] = self._prev_allowed

        if self._prev_base is None:
            os.environ.pop("SOS_TOOL_BASE_DIR", None)
        else:
            os.environ["SOS_TOOL_BASE_DIR"] = self._prev_base

    def test_read_file_is_allowed_under_sos_home(self):
        response = self.client.post(
            "/tools/read_file/execute",
            json={"arguments": {"path": "allowed.txt"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["output"], "hello")

    def test_read_file_is_blocked_outside_allowed_roots(self):
        response = self.client.post(
            "/tools/read_file/execute",
            json={"arguments": {"path": str(self.blocked_file)}},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "path_outside_allowed_roots")

    def test_execute_code_python(self):
        response = self.client.post(
            "/tools/execute_code/execute",
            json={"arguments": {"language": "python", "code": "print('ok')"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertIn("ok", response.json()["output"])


if __name__ == "__main__":
    unittest.main()

