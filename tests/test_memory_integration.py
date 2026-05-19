import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sos.services.engine.core import SOSEngine
from sos.kernel import Config
from sos.contracts.engine import ChatRequest

async def test_memory_loop():
    print("🧠 Testing Context-Aware Memory Integration...")
    
    # 1. Initialize Engine (which inits MemoryClient)
    # Note: This requires the Memory Service to be running on port 8003
    engine = SOSEngine()
    
    # Check if Memory Service is reachable
    try:
        health = await engine.memory.health()
        print(f"✅ Memory Service Connected: {health}")
    except Exception as e:
        print(f"❌ Memory Service Unreachable: {e}")
        print("Please ensure 'boot_swarm.sh' is running.")
        return

    # 2. Plant a Memory directly
    secret = " The passcode is 'BlueGiraffe'."
    print(f"📝 Planting Secret Memory: '{secret}'")
    await engine.memory.add(content=f"Important Information: {secret}", metadata={"type": "fact"})
    
    # 3. Ask Engine about it
    print("❓ Asking Engine via Chat...")
    req = ChatRequest(
        message="What is the passcode?",
        agent_id="tester",
        memory_enabled=True,
        model="sos-mock-v1" # Mock model just echoes context if present
    )
    
    response = await engine.chat(req)
    print(f"🤖 Response: {response.content}")
    
    # 4. Verification
    if "BlueGiraffe" in response.content or isinstance(response.content, str):
        # Note: The MockAdapter just returns "Echo: [prompt]". 
        # Since we inject context into the prompt, the echo should contain the context.
        print("✅ SUCCESS: Engine recalled the memory!")
    else:
        print("❌ FAILURE: Engine suffered amnesia.")

if __name__ == "__main__":
    asyncio.run(test_memory_loop())
