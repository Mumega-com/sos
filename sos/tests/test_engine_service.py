import unittest

from fastapi.testclient import TestClient

from sos.observability.tracing import TRACE_ID_HEADER, SPAN_ID_HEADER
from sos.services.engine.app import app


class TestEngineService(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "engine")
        self.assertNotEqual(response.headers.get(TRACE_ID_HEADER, ""), "")
        self.assertNotEqual(response.headers.get(SPAN_ID_HEADER, ""), "")

    def test_models_endpoint(self):
        response = self.client.get("/models")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertIn("name", data[0])

    def test_chat_endpoint(self):
        response = self.client.post(
            "/chat",
            json={"message": "hi", "agent_id": "agent:test"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["agent_id"], "agent:test")
        self.assertIn("conversation_id", data)
        self.assertTrue(data["content"].startswith("(stub:"))

    def test_metrics_endpoint(self):
        # Prime a request so counters exist.
        self.client.get("/health")

        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("# TYPE sos_requests_total counter", text)
        self.assertIn("# TYPE sos_request_duration_seconds histogram", text)


if __name__ == "__main__":
    unittest.main()

