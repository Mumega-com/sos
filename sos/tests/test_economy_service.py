import unittest

from fastapi.testclient import TestClient

from sos.observability.tracing import TRACE_ID_HEADER, SPAN_ID_HEADER
from sos.services.economy.app import app


class TestEconomyService(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "economy")
        self.assertNotEqual(response.headers.get(TRACE_ID_HEADER, ""), "")
        self.assertNotEqual(response.headers.get(SPAN_ID_HEADER, ""), "")

    def test_payout_affects_balance(self):
        payout = self.client.post(
            "/payout",
            json={
                "agent_id": "agent:test",
                "amount": 100,
                "currency": "MIND",
                "task_id": "task_001",
                "reason": "test",
                "requires_witness": False,
            },
        )
        self.assertEqual(payout.status_code, 200)

        balance = self.client.get("/balance/agent:test?currency=MIND")
        self.assertEqual(balance.status_code, 200)
        self.assertEqual(balance.json()["available"], 100)

    def test_witness_approve_commits_pending_tx(self):
        payout = self.client.post(
            "/payout",
            json={
                "agent_id": "agent:wit",
                "amount": 50,
                "currency": "MIND",
                "task_id": "task_002",
                "reason": "test",
                "requires_witness": True,
            },
        )
        self.assertEqual(payout.status_code, 200)
        tx = payout.json()
        self.assertEqual(tx["status"], "pending_witness")

        approved = self.client.post(
            f"/transactions/{tx['id']}/witness/approve",
            json={"witness_id": "agent:river"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "committed")

        balance = self.client.get("/balance/agent:wit?currency=MIND")
        self.assertEqual(balance.status_code, 200)
        self.assertEqual(balance.json()["available"], 50)

    def test_metrics_endpoint(self):
        self.client.get("/health")
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("# TYPE sos_requests_total counter", response.text)


if __name__ == "__main__":
    unittest.main()

