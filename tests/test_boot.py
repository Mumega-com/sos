import asyncio
import os
import sys

# Setup Path
sys.path.append(os.getcwd())

from sos.services.engine.core import SOSEngine
from sos.services.memory.core import MemoryCore

async def test_boot():
    print(">>> Booting Memory Core...")
    mem = MemoryCore()
    print(f"Memory Health: {await mem.health()}")
    
    print("\n>>> Booting Engine Core...")
    try:
        eng = SOSEngine()
        print(f"Engine Health: {await eng.health()}")
        print("✅ Engine Boot Successful")
    except Exception as e:
        print(f"❌ Engine Boot Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_boot())

