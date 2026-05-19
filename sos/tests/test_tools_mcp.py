import os
import unittest

from fastapi.testclient import TestClient

from sos.services.tools.app import _MCP_SERVER_TOOLS, _MCP_SERVERS, app


class TestToolsMCP(unittest.TestCase):
    def setUp(self):
        self._prev_mode = os.environ.get("SOS_MCP_DISCOVERY_MODE")
        os.environ["SOS_MCP_DISCOVERY_MODE"] = "manual"

        _MCP_SERVERS.clear()
        _MCP_SERVER_TOOLS.clear()
        self.client = TestClient(app)

    def tearDown(self):
        if self._prev_mode is None:
            os.environ.pop("SOS_MCP_DISCOVERY_MODE", None)
        else:
            os.environ["SOS_MCP_DISCOVERY_MODE"] = self._prev_mode

        _MCP_SERVERS.clear()
        _MCP_SERVER_TOOLS.clear()

    def test_register_list_discover_and_delete(self):
        registered = self.client.post(
            "/mcp/servers",
            json={"name": "local", "url": "http://127.0.0.1:9999", "transport": "http"},
        )
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.json()["name"], "local")

        servers = self.client.get("/mcp/servers")
        self.assertEqual(servers.status_code, 200)
        self.assertTrue(any(s["name"] == "local" for s in servers.json()))

        discovered = self.client.post(
            "/mcp/servers/local/discover",
            json={
                "tools": [
                    {
                        "name": "hello",
                        "description": "Test tool",
                        "category": "custom",
                        "parameters": {"type": "object", "properties": {}},
                        "returns": "ok",
                    }
                ]
            },
        )
        self.assertEqual(discovered.status_code, 200)
        self.assertTrue(any(t["name"] == "mcp.local.hello" for t in discovered.json()))

        tools = self.client.get("/tools")
        self.assertEqual(tools.status_code, 200)
        self.assertTrue(any(t["name"] == "mcp.local.hello" for t in tools.json()))

        tool = self.client.get("/tools/mcp.local.hello")
        self.assertEqual(tool.status_code, 200)
        self.assertEqual(tool.json()["name"], "mcp.local.hello")
        self.assertEqual(tool.json()["metadata"]["provider"], "mcp")

        deleted = self.client.delete("/mcp/servers/local")
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])

    def test_discover_requires_tools_in_manual_mode(self):
        self.client.post(
            "/mcp/servers",
            json={"name": "local", "url": "http://127.0.0.1:9999", "transport": "http"},
        )
        response = self.client.post("/mcp/servers/local/discover", json={"tools": []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "missing_tools")


if __name__ == "__main__":
    unittest.main()

