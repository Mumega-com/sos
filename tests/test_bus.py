
import asyncio
from sos.kernel.bus import MessageBus
from sos.kernel import Message, MessageType

async def test_nervous_system():
    print("--- Testing SOS Nervous System (Redis) ---")
    
    bus = MessageBus()
    await bus.connect()
    
    if not bus._redis:
        print("⚠️  Skipping test: Redis not available")
        return

    # 1. Hippocampus Test (Memory)
    print("\n[Test 1] Short-Term Memory (Hippocampus)")
    agent_id = "test_agent_001"
    
    await bus.memory_push(agent_id, "I need to analyze the kernel.", role="user")
    await bus.memory_push(agent_id, "Scanning sos/kernel/config.py...", role="assistant")
    
    memories = await bus.memory_recall(agent_id)
    print(f" > Recalled {len(memories)} recent thoughts:")
    for m in memories:
        print(f"   - [{m['role']}] {m['content']}")

    # 2. Telepathy Test (Pub/Sub)
    print("\n[Test 2] Telepathy (Pub/Sub)")
    
    # Listener Task
    async def synapse_listener():
        print("   👂 Synapse listening...")
        async for msg in bus.subscribe(agent_id, squads=["marketing"]):
            print(f"   ⚡ SIGNAL RECEIVED: [{msg.type.value}] from {msg.source}: {msg.payload}")
            break # Exit after one message
            
    listener = asyncio.create_task(synapse_listener())
    await asyncio.sleep(0.5) # Wait for subscription
    
    # Sender
    msg = Message(
        type=MessageType.CHAT,
        source="hive_mind",
        target=agent_id,
        payload={"instruction": "Execute Order 66"}
    )
    print(f"   🗣️  Broadcasting signal to {agent_id}...")
    await bus.send(msg)
    
    await listener
    await bus.disconnect()
    print("\n✅ Nervous System Operational.")

if __name__ == "__main__":
    asyncio.run(test_nervous_system())
