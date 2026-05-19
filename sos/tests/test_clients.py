import json
import unittest

import httpx
from nacl.signing import SigningKey

from sos.clients.engine import EngineClient
from sos.clients.base import SOSClientError
from sos.contracts.engine import ChatRequest
from sos.kernel.capability import Capability, CapabilityAction, sign_capability
from sos.observability.tracing import TRACE_ID_HEADER, SPAN_ID_HEADER, TraceSpan


class TestClients(unittest.TestCase):
    def test_engine_health_injects_trace_headers(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["trace_id"] = request.headers.get(TRACE_ID_HEADER, "")
            seen["span_id"] = request.headers.get(SPAN_ID_HEADER, "")
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(handler)
        client = EngineClient(base_url="http://engine.local", transport=transport)
        try:
            with TraceSpan("health"):
                data = client.health()
        finally:
            client.close()

        self.assertEqual(data["status"], "ok")
        self.assertNotEqual(seen["trace_id"], "")
        self.assertNotEqual(seen["span_id"], "")

    def test_engine_chat_serializes_capability(self):
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            self.assertIn("capability", payload)
            self.assertEqual(payload["capability"]["action"], CapabilityAction.MEMORY_READ.value)
            self.assertEqual(payload["capability"]["resource"], "memory:kasra/*")
            return httpx.Response(
                200,
                json={
                    "content": "hi",
                    "agent_id": payload["agent_id"],
                    "model_used": "test",
                    "conversation_id": payload.get("conversation_id") or "conv_001",
                },
            )

        transport = httpx.MockTransport(handler)
        client = EngineClient(base_url="http://engine.local", transport=transport)
        try:
            signing_key = SigningKey.generate()
            cap = Capability(
                subject="agent:kasra",
                action=CapabilityAction.MEMORY_READ,
                resource="memory:kasra/*",
            )
            sign_capability(cap, signing_key)

            request = ChatRequest(
                message="hello",
                agent_id="agent:kasra",
                conversation_id="conv_001",
                capability=cap,
            )
            response = client.chat(request)
        finally:
            client.close()

        self.assertEqual(response.content, "hi")
        self.assertEqual(response.agent_id, "agent:kasra")
        self.assertEqual(response.conversation_id, "conv_001")

    def test_error_status_raises_client_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        transport = httpx.MockTransport(handler)
        client = EngineClient(base_url="http://engine.local", transport=transport)
        try:
            with self.assertRaises(SOSClientError) as ctx:
                client.health()
        finally:
            client.close()

        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
