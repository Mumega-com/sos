import unittest

from fastapi.testclient import TestClient

from sos.observability.tracing import TRACE_ID_HEADER, SPAN_ID_HEADER
from sos.services.memory.app import app


class TestMemoryService(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "memory")
        self.assertNotEqual(response.headers.get(TRACE_ID_HEADER, ""), "")
        self.assertNotEqual(response.headers.get(SPAN_ID_HEADER, ""), "")

    def test_store_search_get_delete(self):
        store = self.client.post(
            "/store",
            json={"content": "hello world", "agent_id": "agent:test"},
        )
        self.assertEqual(store.status_code, 200)
        memory_id = store.json()["memory_id"]

        search = self.client.post(
            "/search",
            json={"query": "hello", "agent_id": "agent:test"},
        )
        self.assertEqual(search.status_code, 200)
        results = search.json()
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["memory"]["id"], memory_id)

        get_mem = self.client.get(f"/memories/{memory_id}")
        self.assertEqual(get_mem.status_code, 200)
        self.assertEqual(get_mem.json()["content"], "hello world")

        deleted = self.client.delete(f"/memories/{memory_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])

    def test_metrics_endpoint(self):
        self.client.get("/health")
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("# TYPE sos_requests_total counter", response.text)


if __name__ == "__main__":
    unittest.main()

