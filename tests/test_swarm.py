
import asyncio
from sos.services.engine.swarm import SwarmDispatcher

async def test_swarm():
    print("--- Testing Swarm Dispatcher ---")
    
    swarm = SwarmDispatcher()
    
    # 1. Shard a complex objective
    print("\n[Action] Sharding Objective: 'Launch the new Mumega Website'")
    task_ids = await swarm.shard_objective("Launch the new Mumega Website")
    
    print(f"[Result] Created {len(task_ids)} shards:")
    for tid in task_ids:
        print(f" - {tid}")

    # 2. Verify Persistence
    print("\n[Action] Reading Pending Tasks from Repository...")
    pending = await swarm.list_pending_tasks()
    
    print(f"[Result] Found {len(pending)} pending tasks.")
    for t in pending:
        print(f" - [{t['status'].upper()}] {t['title']} (Bounty: {t['bounty']['amount']} {t['bounty']['token']})")

if __name__ == "__main__":
    asyncio.run(test_swarm())
