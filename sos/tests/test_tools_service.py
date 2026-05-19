import unittest

from fastapi.testclient import TestClient

from sos.observability.tracing import TRACE_ID_HEADER, SPAN_ID_HEADER
from sos.services.tools.app import app


class TestToolsService(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "tools")
        self.assertNotEqual(response.headers.get(TRACE_ID_HEADER, ""), "")
        self.assertNotEqual(response.headers.get(SPAN_ID_HEADER, ""), "")

    def test_list_tools_and_get_tool(self):
        response = self.client.get("/tools")
        self.assertEqual(response.status_code, 200)
        tools = response.json()
        self.assertIsInstance(tools, list)
        self.assertTrue(any(t["name"] == "web_search" for t in tools))

        tool = self.client.get("/tools/web_search")
        self.assertEqual(tool.status_code, 200)
        self.assertEqual(tool.json()["name"], "web_search")

    def test_execute_tool_not_implemented(self):
        response = self.client.post("/tools/web_search/execute", json={"arguments": {"query": "hi"}})
        self.assertEqual(response.status_code, 501)

    def test_metrics_endpoint(self):
        self.client.get("/health")
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("# TYPE sos_requests_total counter", response.text)


if __name__ == "__main__":
    unittest.main()

