
"""
Sovereign Task Manager (Engine Adapter)
Ported logic from cli_old/mumega/core/sovereign_task_manager.py to fit SOS Architecture.
"""

import logging
import uuid
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from sos.kernel import Config
from sos.observability.logging import get_logger

log = get_logger("task_manager")

class SovereignTaskManager:
    """
    Manages proactive task creation for the SOSEngine.
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.tasks_dir = self.config.paths.tasks_dir
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def is_complex_request(self, message: str) -> bool:
        """
        Heuristic to detect if a message warrants a persistent Task.
        """
        if not message or len(message) < 20:
            return False
            
        msg_lower = message.lower()
        
        # Triggers
        triggers = [
            "build", "create", "implement", "refactor", "migrate",
            "deploy", "setup", "integrate", "architect", "scale",
            "plan", "strategy"
        ]
        
        count = sum(1 for t in triggers if t in msg_lower)
        return count >= 1

    async def create_task_from_request(self, message: str, agent_id: str) -> str:
        """
        Automatically spawns a task from a chat message.
        """
        # Generate ID
        task_id = f"task_auto_{uuid.uuid4().hex[:8]}"
        filename = self.tasks_dir / f"{task_id}.json"
        
        # Extract rudimentary title
        words = message.split()
        title = " ".join(words[:6]) + "..." if len(words) > 6 else message
        
        import json
        task_data = {
            "id": task_id,
            "title": f"Auto: {title}",
            "owner": agent_id,
            "status": "pending",
            "priority": "normal",
            "scope": "Auto-Detected",
            "description": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "auto_spawned": True,
            "bounty": {"token": "MIND", "amount": 10}
        }
        
        with open(filename, "w") as f:
            json.dump(task_data, f, indent=2)
            
        log.info(f"✨ Auto-spawned task {task_id} for agent {agent_id}")
        return task_id
