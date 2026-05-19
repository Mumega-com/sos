
import asyncio
from sos.services.engine.core import SOSEngine
from sos.contracts.engine import ChatRequest

async def test_witness():
    print("--- Testing Witness Protocol Integration ---")
    engine = SOSEngine()
    
    # 1. Standard Chat (No Witness)
    print("\n[Test 1] Standard Chat")
    req1 = ChatRequest(message="Hello World", agent_id="tester", witness_enabled=False)
    resp1 = await engine.chat(req1)
    print(f"Response: {resp1.content}")
    
    # 2. Witnessed Chat
    print("\n[Test 2] Witnessed Chat (Human-in-the-Loop)")
    req2 = ChatRequest(message="Launch the nuke", agent_id="tester", witness_enabled=True)
    resp2 = await engine.chat(req2)
    print(f"Response: {resp2.content}")
    # Note: In a real system, we'd inspect resp2.metadata for witness stats
    
if __name__ == "__main__":
    asyncio.run(test_witness())
