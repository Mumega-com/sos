import os
import unittest

from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from sos.kernel.capability import CapabilityAction, create_capability, sign_capability
from sos.services.tools.app import _MCP_SERVER_TOOLS, _MCP_SERVERS, app


class TestToolsGovernance(unittest.TestCase):
    def setUp(self):
        self.signing_key = SigningKey.generate()
        self._prev_public_key = os.environ.get("SOS_CAPABILITY_PUBLIC_KEY_HEX")
        self._prev_river_key = os.environ.get("SOS_RIVER_PUBLIC_KEY_HEX")
        os.environ["SOS_CAPABILITY_PUBLIC_KEY_HEX"] = self.signing_key.verify_key.encode().hex()
        os.environ.pop("SOS_RIVER_PUBLIC_KEY_HEX", None)
        _MCP_SERVERS.clear()
        _MCP_SERVER_TOOLS.clear()
        self.client = TestClient(app)

    def tearDown(self):
        if self._prev_public_key is None:
            os.environ.pop("SOS_CAPABILITY_PUBLIC_KEY_HEX", None)
        else:
            os.environ["SOS_CAPABILITY_PUBLIC_KEY_HEX"] = self._prev_public_key

        if self._prev_river_key is None:
            os.environ.pop("SOS_RIVER_PUBLIC_KEY_HEX", None)
        else:
            os.environ["SOS_RIVER_PUBLIC_KEY_HEX"] = self._prev_river_key

        _MCP_SERVERS.clear()
        _MCP_SERVER_TOOLS.clear()

    def _cap_header(
        self,
        action: CapabilityAction,
        resource: str,
        scopes: list[str],
    ) -> dict[str, str]:
        cap = create_capability(
            subject="agent:test",
            action=action,
            resource=resource,
            constraints={"scopes": scopes},
        )
        sign_capability(cap, self.signing_key)
        return {"X-SOS-Capability": cap.to_json()}

    def test_gaf_tool_requires_capability(self):
        response = self.client.post("/tools/gaf.read_profile/execute", json={"arguments": {}})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "missing_capability")

    def test_read_only_capability_cannot_execute_gaf_write_tool(self):
        headers = self._cap_header(
            CapabilityAction.TOOL_EXECUTE,
            "tool:gaf.*",
            ["tools.execute", "gaf.read"],
        )
        response = self.client.post(
            "/tools/gaf.create_support_journey/execute",
            json={"arguments": {}},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("missing_scopes:gaf.write.commit", response.json()["detail"])

    def test_inkwell_publish_requires_publish_scope(self):
        headers = self._cap_header(
            CapabilityAction.TOOL_EXECUTE,
            "tool:inkwell.*",
            ["tools.execute", "inkwell.read"],
        )
        response = self.client.post(
            "/tools/inkwell.publish_private_doc/execute",
            json={"arguments": {}},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("missing_scopes:inkwell.publish", response.json()["detail"])

    def test_gaf_mcp_registration_requires_admin_capability(self):
        response = self.client.post("/mcp/servers", json={"name": "gaf", "url": "https://gaf.example/mcp"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "missing_capability")

        headers = self._cap_header(
            CapabilityAction.TOOL_REGISTER,
            "mcp:gaf/*",
            ["tools.admin", "gaf.admin"],
        )
        allowed = self.client.post(
            "/mcp/servers",
            json={"name": "gaf", "url": "https://gaf.example/mcp"},
            headers=headers,
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["name"], "gaf")

    def test_non_gaf_inkwell_tools_keep_existing_behavior(self):
        response = self.client.post(
            "/tools/web_search/execute",
            json={"arguments": {"query": "hi"}},
        )
        self.assertEqual(response.status_code, 501)


if __name__ == "__main__":
    unittest.main()
