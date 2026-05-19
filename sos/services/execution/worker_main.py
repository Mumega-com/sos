
import asyncio
from sos.services.execution.worker import get_worker

if __name__ == "__main__":
    worker = get_worker()
    asyncio.run(worker.start())
