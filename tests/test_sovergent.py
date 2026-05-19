
import asyncio
from sos.services.engine.core import SOSEngine
from sos.contracts.engine import ChatRequest

async def test_sovergent_tasks():
    print("--- Testing Sovergent Task Management ---")
    engine = SOSEngine()
    
    # 1. Simple Request (Should NOT spawn task)
    print("\n[Test 1] Simple Request: 'Hi'")
    req1 = ChatRequest(message="Hi there", agent_id="tester")
    await engine.chat(req1) # Check logs for absence of "Auto-spawned" 
    
    # 2. Complex Request (Should spawn task)
    print("\n[Test 2] Complex Request: 'Build a new architecture'")
    req2 = ChatRequest(message="We need to build and deploy a new scaling architecture.", agent_id="tester")
    await engine.chat(req2) # Check logs for "Auto-spawned"

if __name__ == "__main__":
    asyncio.run(test_sovergent_tasks())
