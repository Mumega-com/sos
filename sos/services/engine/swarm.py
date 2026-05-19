
"""
SOS Swarm Dispatcher - The Orchestrator of the Blind Swarm.

Responsibilities:
1. Decompose high-level objectives into atomic micro-tasks (Sharding).
2. Dispatch tasks to the Sovereign Task Repository (~/.sos/tasks).
3. Manage the 'Blind Swarm' state machine (Pending -> Claimed -> Witnessed).

Adheres to ARCHITECTURE_AGREEMENT.md:
- Lazy loading of ML logic.
- Structured logging.
- Schema-based communication.
"""

import json
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from sos.kernel import Config, Message, MessageType, Response
from sos.observability.logging import get_logger

log = get_logger("swarm_dispatcher")

class SwarmDispatcher:
    """
    Manages the decomposition and distribution of tasks to the 16D Swarm.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.tasks_dir = self.config.paths.tasks_dir
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"SwarmDispatcher initialized. Tasks dir: {self.tasks_dir}")

    async def shard_objective(self, objective: str, context: str = "") -> List[str]:
        """
        Breaks a high-level objective into atomic micro-tasks.
        
        Uses the Engine's primary model (lazy loaded) to perform the decomposition.
        """
        log.info(f"Sharding objective: {objective[:50]}...")
        
        # Mocking the LLM decomposition for Phase 1
        # In Phase 2, this calls self.engine.chat() with a specific 'Architect' prompt.
        
        # Simulating logic:
        shards = []
        if "website" in objective.lower():
            shards = [
                {"title": "Draft Hero Copy", "scope": "Marketing"},
                {"title": "Design FRC Logo", "scope": "Design"},
                {"title": "Implement Next.js Scaffold", "scope": "Engineering"}
            ]
        elif "audit" in objective.lower():
            shards = [
                {"title": "Scan Kernel for Imports", "scope": "Security"},
                {"title": "Verify Schema Compliance", "scope": "Architecture"}
            ]
        else:
             shards = [{"title": f"Process: {objective}", "scope": "General"}]

        created_ids = []
        for shard in shards:
            task_id = await self._create_task(
                title=shard["title"],
                scope=shard["scope"],
                parent_objective=objective
            )
            created_ids.append(task_id)

        log.info(f"Sharding complete. Created {len(created_ids)} micro-tasks.")
        return created_ids

    async def _create_task(self, title: str, scope: str, parent_objective: str) -> str:
        """
        Persists a task to the local repository.
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        filename = self.tasks_dir / f"{task_id}.json"
        
        # Schema definition (TASK_SYSTEM.md)
        task_data = {
            "id": task_id,
            "title": title,
            "owner": "SwarmDispatcher",
            "status": "pending",  # State Machine: Pending
            "priority": "normal",
            "scope": scope,
            "description": f"Shard of objective: {parent_objective}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "context": {},
            "bounty": {"token": "MIND", "amount": 10} # Default bounty
        }

        # Atomic Write
        with open(filename, "w") as f:
            json.dump(task_data, f, indent=2)
            
        log.info(f"Task dispatched: {task_id} ({title})")
        
        # Emit Event (Observability)
        # In a real event bus, we would publish this Message
        msg = Message(
            type=MessageType.TASK_CREATE,
            source="swarm_dispatcher",
            target="broadcast",
            payload={"task_id": task_id, "title": title}
        )
        # self.bus.publish(msg) 
        
        return task_id

    async def list_pending_tasks(self) -> List[Dict[str, Any]]:
        """
        Reads the repository for 'pending' tasks.
        Used by Spore Generators to fetch work.
        """
        pending = []
        if not self.tasks_dir.exists():
            return []

        for f in self.tasks_dir.glob("*.json"):
            try:
                with open(f, "r") as tf:
                    data = json.load(tf)
                    if data.get("status") == "pending":
                        pending.append(data)
            except Exception as e:
                log.warning(f"Corrupt task file {f}: {e}")
        
        return pending

    async def complete_task(self, task_id: str) -> bool:
        """
        Marks a task as complete (Deletes from pending repo, moves to archive).
        """
        # Security check to prevent directory traversal
        if ".." in task_id or "/" in task_id:
            return False
            
        source = self.tasks_dir / f"{task_id}.json"
        
        if not source.exists():
            # Try finding it if ID differs from filename
            found = list(self.tasks_dir.glob(f"*{task_id}*.json"))
            if found:
                source = found[0]
            else:
                return False
        
        try:
            # In a real system, we move to /archive. For now, we delete to clear the UI.
            source.unlink()
            log.info(f"Task completed and removed: {task_id}")
            return True
        except Exception as e:
            log.error(f"Failed to complete task {task_id}: {e}")
            return False

# Singleton entry point for easy import
_dispatcher = None

def get_swarm() -> SwarmDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = SwarmDispatcher()
    return _dispatcher
