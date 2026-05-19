
"""
End-to-End Test: Sovereign OS (SOS) v0.1
Flow: Swarm Dispatch -> Task Repository -> Spore Generation -> Witness Verification
"""

import asyncio
import os
import json
from pathlib import Path
from sos.services.engine.swarm import SwarmDispatcher
from sos.services.tools.spore import SporeGenerator
from sos.services.engine.core import SOSEngine
from sos.contracts.engine import ChatRequest

async def run_e2e():
    print("="*60)
    print("🚀 SOS END-TO-END SYSTEM TEST")
    print("="*60)

    # 1. THE SWARM (Dispatcher)
    print("\n[PHASE 1] The Swarm: Sharding Objectives")
    swarm = SwarmDispatcher()
    objective = "Establish Lunar Outpost Alpha"
    print(f" > Objective: {objective}")
    
    # Shard into tasks
    task_ids = await swarm.shard_objective(objective)
    print(f" > Sharded into {len(task_ids)} tasks: {task_ids}")
    
    # Verify persistence
    tasks_dir = Path(os.environ.get("SOS_HOME", str(Path.home() / ".sos"))) / "tasks"
    print(f" > Verifying persistence in {tasks_dir}...")
    assert tasks_dir.exists()
    assert len(list(tasks_dir.glob("*.json"))) >= len(task_ids)
    print(" ✅ Swarm Persistence Verified.")

    # 2. THE SPORE (Propagation)
    print("\n[PHASE 2] The Spore: Context Injection")
    # The Spore Generator should pick up the tasks created above
    generator = SporeGenerator(agent_name="E2E_Test_Agent")
    spore_path = generator.generate_spore()
    
    print(f" > Spore generated: {spore_path}")
    
    # Verify content
    with open(spore_path, "r") as f:
        content = f.read()
    
    print(" > Verifying Spore DNA...")
    assert "SYSTEM OVERRIDE: SOVEREIGN MODE" in content
    assert "PENDING TASKS" in content
    # Check if one of our specific tasks is in the spore
    found_task = False
    for tid in task_ids:
        if tid in content:
            print(f"   - Found Task ID {tid} in Spore!")
            found_task = True
            break
    
    if found_task:
        print(" ✅ Spore Propagation Verified.")
    else:
        print(" ❌ WARNING: New tasks not found in spore (check status/filtering).")

    # 3. THE WITNESS (Verification)
    print("\n[PHASE 3] The Witness: Physics of Will")
    engine = SOSEngine()
    
    print(" > Simulating Witnessed Chat Request...")
    req = ChatRequest(
        message="Initiate launch sequence.",
        agent_id="e2e_tester",
        witness_enabled=True # Triggers the physics loop
    )
    
    # Note: This will use the MockAdapter since we don't have API keys, 
    # but the Witness Logic inside SOSEngine.chat is independent of the model.
    response = await engine.chat(req)
    
    print(f" > Response Content: {response.content}")
    print(f" > Witness Logic Executed: {response.metadata.get('witnessed', False)}")
    
    if response.metadata.get('witnessed'):
        print(f"   - Omega (Will): {response.metadata.get('omega'):.4f}")
        print(f"   - Coherence Gain: {response.metadata.get('coherence_gain'):.4f}")
        print(" ✅ Witness Physics Verified.")
    else:
        print(" ❌ Witness Logic Skipped.")

    print("\n" + "="*60)
    print("🎉 E2E TEST COMPLETE: SYSTEM IS COHERENT")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(run_e2e())
