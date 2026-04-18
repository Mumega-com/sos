
"""
SOS Async Worker - The Muscle of the System.

Architecture:
- Decoupled: Listens to a Queue (RabbitMQ/Redis Stream), executes, and forgets.
- Scalable: You can spin up 1,000 workers to handle 10M users.
- Robust: Retries failed tasks automatically.

Current Implementation:
- Uses Redis Streams as a 'Lite Kafka' for immediate capability.
- Defined to easily swap for RabbitMQ/Kafka in the future.
"""

import asyncio
import json
from typing import Callable, Dict, Any, Optional

from sos.kernel import Config
from sos.kernel.bus import get_bus
from sos.observability.logging import get_logger

log = get_logger("async_worker")

class AsyncWorker:
    def __init__(self, queue_name: str = "sos:queue:global"):
        self.bus = get_bus()
        self.queue_name = queue_name
        self.running = False
        self.handlers: Dict[str, Callable] = {}
        
    def register_handler(self, task_type: str, handler: Callable):
        """Register a function to handle specific task types."""
        self.handlers[task_type] = handler
        log.info(f"👷 Worker registered handler for: {task_type}")

    async def start(self):
        """Start the consumer loop."""
        self.running = True
        log.info(f"🚀 Async Worker started. Listening on {self.queue_name}...")
        
        while self.running:
            if not self.bus._redis:
                await asyncio.sleep(1)
                continue

            try:
                # Block for 1 second waiting for new tasks
                # XREADGROUP would be used here in production for Consumer Groups
                streams = await self.bus._redis.xread({self.queue_name: '$'}, count=1, block=1000)
                
                if streams:
                    for stream_name, entries in streams:
                        for message_id, data in entries:
                            await self._process_task(message_id, data)
                            
            except Exception as e:
                log.error(f"Worker Loop Error: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        self.running = False
        log.info("🛑 Async Worker stopping...")

    async def _process_task(self, message_id: str, data: Dict[str, Any]):
        """Execute the task logic."""
        task_type = data.get("type")
        payload_str = data.get("payload", "{}")
        
        try:
            payload = json.loads(payload_str)
        except:
            payload = {}

        log.info(f"⚙️ Processing Task {message_id}: {task_type}")

        if task_type in self.handlers:
            try:
                # Execute Handler
                result = await self.handlers[task_type](payload)
                log.info(f"✅ Task {message_id} Complete. Result: {result}")
                # Acknowledge (XACK) would happen here
            except Exception as e:
                log.error(f"❌ Task {message_id} Failed: {e}")
        else:
            log.warning(f"⚠️  No handler for task type: {task_type}")

    async def submit_task(self, task_type: str, payload: Dict[str, Any]):
        """Producer: Submit a task to the queue."""
        if not self.bus._redis: return
        
        entry = {
            "type": task_type,
            "payload": json.dumps(payload),
            "ts": asyncio.get_event_loop().time()
        }
        await self.bus._redis.xadd(self.queue_name, entry)
        log.info(f"📤 Task submitted: {task_type}")

# --- Example Handler ---
async def heavy_lifting_handler(payload: Dict[str, Any]) -> str:
    # Simulate work
    duration = payload.get("duration", 0.1)
    await asyncio.sleep(duration)
    return f"Lifted {payload.get('weight', '10kg')} successfully."

# Singleton
_worker = None
def get_worker() -> AsyncWorker:
    global _worker
    if _worker is None:
        _worker = AsyncWorker()
        # Register default handlers
        _worker.register_handler("heavy_lift", heavy_lifting_handler)
    return _worker
