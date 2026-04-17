
import asyncio
from sos.services.engine.council import create_council
from sos.services.execution.worker import get_worker
from sos.kernel.bus import get_bus

async def test_council_and_worker():
    print("--- Testing Swarm Council & Async Worker ---")
    
    # 1. Setup
    bus = get_bus()
    await bus.connect()
    
    if not bus._redis:
        print("⚠️  Skipping test: Redis not available")
        return

    # 2. Start Worker (Background)
    worker = get_worker()
    worker_task = asyncio.create_task(worker.start())
    
    # 3. Council Session
    print("\n[Phase 1] The Council Session")
    squad_id = "marketing_squad"
    council = create_council(squad_id)
    
    # Agent A Proposes
    proposal_id = await council.propose(
        agent_id="agent_alice",
        title="Launch Website",
        payload={"task": "deploy_website"}
    )
    print(f" > Proposal Created: {proposal_id}")
    
    # Agent B Votes Yes
    res = await council.vote("agent_bob", proposal_id, "yes")
    print(f" > Agent Bob Voted: {res}")
    
    # Agent C Votes Yes (Triggering Consensus)
    res = await council.vote("agent_charlie", proposal_id, "yes")
    print(f" > Agent Charlie Voted: {res}")  # Should show "Proposal passed"

    # 4. Async Execution
    print("\n[Phase 2] Async Execution")
    # Submit a task to the worker
    await worker.submit_task("heavy_lift", {"weight": "500kg", "duration": 0.5})
    
    # Wait for processing
    print(" > Waiting for worker...")
    await asyncio.sleep(1.0) 
    
    # Cleanup
    await worker.stop()
    await worker_task
    await bus.disconnect()
    print("\n✅ System Scalability Test Complete.")

if __name__ == "__main__":
    asyncio.run(test_council_and_worker())
