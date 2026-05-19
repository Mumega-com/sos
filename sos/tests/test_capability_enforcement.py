import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from sos.kernel import Capability, CapabilityAction, sign_capability
from sos.services.economy.app import app as economy_app
from sos.services.memory.app import app as memory_app
from sos.services.tools.app import app as tools_app


class TestCapabilityEnforcement(unittest.TestCase):
    def setUp(self):
        self._prev_require = os.environ.get("SOS_REQUIRE_CAPABILITIES")
        self._prev_key = os.environ.get("SOS_RIVER_PUBLIC_KEY_HEX")

        self._signing_key = SigningKey.generate()
        os.environ["SOS_REQUIRE_CAPABILITIES"] = "1"
        os.environ["SOS_RIVER_PUBLIC_KEY_HEX"] = self._signing_key.verify_key.encode().hex()

        self.memory = TestClient(memory_app)
        self.economy = TestClient(economy_app)
        self.tools = TestClient(tools_app)

    def tearDown(self):
        if self._prev_require is None:
            os.environ.pop("SOS_REQUIRE_CAPABILITIES", None)
        else:
            os.environ["SOS_REQUIRE_CAPABILITIES"] = self._prev_require

        if self._prev_key is None:
            os.environ.pop("SOS_RIVER_PUBLIC_KEY_HEX", None)
        else:
            os.environ["SOS_RIVER_PUBLIC_KEY_HEX"] = self._prev_key

    def _capability_dict(self, *, subject: str, action: CapabilityAction, resource: str) -> dict:
        now = datetime.now(timezone.utc)
        cap = Capability(
            subject=subject,
            action=action,
            resource=resource,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            issuer="river",
        )
        sign_capability(cap, self._signing_key)
        return cap.to_dict()

    def _capability_header(self, cap: dict) -> str:
        raw = json.dumps(cap, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def test_memory_store_search_require_capability(self):
        agent_id = "agent:captest"

        missing = self.memory.post("/store", json={"content": "hello world", "agent_id": agent_id})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["detail"], "missing_capability")

        write_cap = self._capability_dict(
            subject=agent_id,
            action=CapabilityAction.MEMORY_WRITE,
            resource=f"memory:{agent_id}/*",
        )
        stored = self.memory.post(
            "/store",
            json={"content": "hello world", "agent_id": agent_id, "capability": write_cap},
        )
        self.assertEqual(stored.status_code, 200)
        memory_id = stored.json()["memory_id"]

        missing_search = self.memory.post("/search", json={"query": "hello", "agent_id": agent_id})
        self.assertEqual(missing_search.status_code, 401)
        self.assertEqual(missing_search.json()["detail"], "missing_capability")

        read_cap = self._capability_dict(
            subject=agent_id,
            action=CapabilityAction.MEMORY_READ,
            resource=f"memory:{agent_id}/*",
        )
        search = self.memory.post(
            "/search",
            json={"query": "hello", "agent_id": agent_id, "capability": read_cap},
        )
        self.assertEqual(search.status_code, 200)
        self.assertGreaterEqual(len(search.json()), 1)

        missing_get = self.memory.get(f"/memories/{memory_id}")
        self.assertEqual(missing_get.status_code, 401)
        self.assertEqual(missing_get.json()["detail"], "missing_capability")

        get_mem = self.memory.get(
            f"/memories/{memory_id}",
            headers={"X-SOS-Capability": self._capability_header(read_cap)},
        )
        self.assertEqual(get_mem.status_code, 200)
        self.assertEqual(get_mem.json()["id"], memory_id)

        delete_cap = self._capability_dict(
            subject=agent_id,
            action=CapabilityAction.MEMORY_DELETE,
            resource=f"memory:{agent_id}/*",
        )
        missing_delete = self.memory.delete(f"/memories/{memory_id}")
        self.assertEqual(missing_delete.status_code, 401)
        self.assertEqual(missing_delete.json()["detail"], "missing_capability")

        deleted = self.memory.delete(
            f"/memories/{memory_id}",
            headers={"X-SOS-Capability": self._capability_header(delete_cap)},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])

    def test_economy_payout_requires_capability(self):
        agent_id = "agent:captest"

        missing = self.economy.post(
            "/payout",
            json={"agent_id": agent_id, "amount": 10, "task_id": "task_1", "reason": "ok"},
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["detail"], "missing_capability")

        cap = self._capability_dict(
            subject=agent_id,
            action=CapabilityAction.LEDGER_WRITE,
            resource="ledger:*",
        )
        paid = self.economy.post(
            "/payout",
            json={
                "agent_id": agent_id,
                "amount": 10,
                "task_id": "task_1",
                "reason": "ok",
                "capability": cap,
            },
        )
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(paid.json()["tx_type"], "payout")

    def test_tools_execute_requires_capability(self):
        agent_id = "agent:captest"

        missing = self.tools.post(
            "/tools/web_search/execute",
            json={"arguments": {"query": "sos"}, "agent_id": agent_id},
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["detail"], "missing_capability")

        cap = self._capability_dict(
            subject=agent_id,
            action=CapabilityAction.TOOL_EXECUTE,
            resource="tool:web_search",
        )
        allowed = self.tools.post(
            "/tools/web_search/execute",
            json={"arguments": {"query": "sos"}, "agent_id": agent_id, "capability": cap},
        )
        self.assertEqual(allowed.status_code, 501)
        self.assertEqual(allowed.json()["detail"], "tool_execution_not_implemented")

    def test_economy_read_endpoints_require_capability(self):
        agent_id = "agent:captest-ledger"

        missing_balance = self.economy.get(f"/balance/{agent_id}")
        self.assertEqual(missing_balance.status_code, 401)
        self.assertEqual(missing_balance.json()["detail"], "missing_capability")

        read_cap = self._capability_dict(
            subject=agent_id,
            action=CapabilityAction.LEDGER_READ,
            resource="ledger:*",
        )
        balance = self.economy.get(
            f"/balance/{agent_id}",
            headers={"X-SOS-Capability": self._capability_header(read_cap)},
        )
        self.assertEqual(balance.status_code, 200)
        self.assertEqual(balance.json()["agent_id"], agent_id)

        missing_txs = self.economy.get(f"/transactions?agent_id={agent_id}")
        self.assertEqual(missing_txs.status_code, 401)
        self.assertEqual(missing_txs.json()["detail"], "missing_capability")

        txs = self.economy.get(
            f"/transactions?agent_id={agent_id}",
            headers={"X-SOS-Capability": self._capability_header(read_cap)},
        )
        self.assertEqual(txs.status_code, 200)


if __name__ == "__main__":
    unittest.main()
