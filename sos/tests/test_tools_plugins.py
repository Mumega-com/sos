import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from sos.artifacts.registry import ArtifactRegistry
from sos.services.tools.app import (
    _MCP_SERVER_TOOLS,
    _MCP_SERVERS,
    _PLUGIN_SIGNATURE_VERIFIED,
    _PLUGIN_POLICY_RESULTS,
    _PLUGIN_TOOLS,
    _PLUGINS,
    app,
)


class TestToolsPlugins(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self._prev_home = os.environ.get("SOS_HOME")
        self._prev_tools_exec = os.environ.get("SOS_TOOLS_EXECUTION_ENABLED")
        self._prev_plugins_exec = os.environ.get("SOS_PLUGINS_EXECUTION_ENABLED")
        self._prev_policy_commands = os.environ.get("SOS_PLUGINS_POLICY_COMMANDS")
        self._prev_policy_enforce = os.environ.get("SOS_PLUGINS_POLICY_ENFORCE")

        os.environ["SOS_HOME"] = self.temp_dir
        os.environ.pop("SOS_TOOLS_EXECUTION_ENABLED", None)
        os.environ.pop("SOS_PLUGINS_EXECUTION_ENABLED", None)
        os.environ.pop("SOS_PLUGINS_POLICY_COMMANDS", None)
        os.environ.pop("SOS_PLUGINS_POLICY_ENFORCE", None)

        _MCP_SERVERS.clear()
        _MCP_SERVER_TOOLS.clear()
        _PLUGINS.clear()
        _PLUGIN_TOOLS.clear()
        _PLUGIN_SIGNATURE_VERIFIED.clear()
        _PLUGIN_POLICY_RESULTS.clear()

        self.workspace = Path(self.temp_dir) / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

        if self._prev_home is None:
            os.environ.pop("SOS_HOME", None)
        else:
            os.environ["SOS_HOME"] = self._prev_home

        if self._prev_tools_exec is None:
            os.environ.pop("SOS_TOOLS_EXECUTION_ENABLED", None)
        else:
            os.environ["SOS_TOOLS_EXECUTION_ENABLED"] = self._prev_tools_exec

        if self._prev_plugins_exec is None:
            os.environ.pop("SOS_PLUGINS_EXECUTION_ENABLED", None)
        else:
            os.environ["SOS_PLUGINS_EXECUTION_ENABLED"] = self._prev_plugins_exec

        if self._prev_policy_commands is None:
            os.environ.pop("SOS_PLUGINS_POLICY_COMMANDS", None)
        else:
            os.environ["SOS_PLUGINS_POLICY_COMMANDS"] = self._prev_policy_commands

        if self._prev_policy_enforce is None:
            os.environ.pop("SOS_PLUGINS_POLICY_ENFORCE", None)
        else:
            os.environ["SOS_PLUGINS_POLICY_ENFORCE"] = self._prev_policy_enforce

        _MCP_SERVERS.clear()
        _MCP_SERVER_TOOLS.clear()
        _PLUGINS.clear()
        _PLUGIN_TOOLS.clear()
        _PLUGIN_SIGNATURE_VERIFIED.clear()
        _PLUGIN_POLICY_RESULTS.clear()

    def _mint_demo_plugin(self) -> str:
        plugin_json = self.workspace / "plugin.json"
        tools_json = self.workspace / "tools.json"
        runner_py = self.workspace / "run_tool.py"

        plugin_json.write_text(
            """
{
  "name": "demo",
  "version": "1.0.0",
  "author": "agent:codex",
  "description": "demo plugin",
  "trust_level": "unsigned",
  "capabilities_required": [],
  "capabilities_provided": [],
  "entrypoints": {
    "tools": "tools.json",
    "execute": "python:run_tool.py"
  }
}
""".strip(),
            encoding="utf-8",
        )
        tools_json.write_text(
            """
[
  {
    "name": "echo",
    "description": "Echo arguments",
    "category": "custom",
    "parameters": {"type": "object", "properties": {"message": {"type": "string"}}},
    "returns": "Echoed message",
    "required_capability": "tool:execute",
    "timeout_seconds": 5,
    "sandbox_required": true
  }
]
""".strip(),
            encoding="utf-8",
        )
        runner_py.write_text(
            """
import json, sys

req = json.loads(sys.stdin.read() or "{}")
args = req.get("arguments", {}) or {}
msg = args.get("message", "")
print(json.dumps({"success": True, "output": msg}))
""".strip(),
            encoding="utf-8",
        )

        registry = ArtifactRegistry()
        manifest = registry.mint(
            task_id="task_plugin_demo",
            version="1.0.0",
            author="agent:codex",
            files=[plugin_json, tools_json, runner_py],
            base_dir=self.workspace,
        )
        return manifest.cid

    def test_install_lists_tools_and_executes_when_enabled(self):
        cid = self._mint_demo_plugin()

        installed = self.client.post("/plugins", json={"cid": cid})
        self.assertEqual(installed.status_code, 200)
        self.assertEqual(installed.json()["name"], "demo")
        self.assertTrue(any(t.startswith("plugin.demo.") for t in installed.json()["tools"]))

        tools = self.client.get("/tools")
        self.assertEqual(tools.status_code, 200)
        self.assertTrue(any(t["name"] == "plugin.demo.echo" for t in tools.json()))

        disabled = self.client.post("/tools/plugin.demo.echo/execute", json={"arguments": {"message": "hi"}})
        self.assertEqual(disabled.status_code, 501)
        self.assertEqual(disabled.json()["detail"], "plugin_execution_not_enabled")

        os.environ["SOS_TOOLS_EXECUTION_ENABLED"] = "1"
        os.environ["SOS_PLUGINS_EXECUTION_ENABLED"] = "1"

        executed = self.client.post("/tools/plugin.demo.echo/execute", json={"arguments": {"message": "hi"}})
        self.assertEqual(executed.status_code, 200)
        self.assertTrue(executed.json()["success"])
        self.assertEqual(executed.json()["output"], "hi")

        removed = self.client.delete(f"/plugins/{cid}")
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["deleted"])

    def test_plugin_install_fails_when_policy_commands_fail(self):
        cid = self._mint_demo_plugin()

        os.environ["SOS_PLUGINS_POLICY_COMMANDS"] = "python -c \"import sys; sys.exit(2)\""

        installed = self.client.post("/plugins", json={"cid": cid})
        self.assertEqual(installed.status_code, 403)
        self.assertEqual(installed.json()["detail"], "plugin_policy_failed")

    def test_plugin_install_records_policy_results_when_passed(self):
        cid = self._mint_demo_plugin()

        os.environ["SOS_PLUGINS_POLICY_COMMANDS"] = "python -c \"print('ok')\""

        installed = self.client.post("/plugins", json={"cid": cid})
        self.assertEqual(installed.status_code, 200)
        self.assertEqual(installed.json()["name"], "demo")
        self.assertIn("policy", installed.json())
        self.assertTrue(installed.json()["policy"]["passed"])


if __name__ == "__main__":
    unittest.main()
