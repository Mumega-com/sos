
import asyncio
import os
import sys
import httpx
import redis.asyncio as redis
from sos.observability.logging import get_logger

# Add project root
sys.path.append(os.getcwd())

from sos.clients.mirror import MirrorClient

log = get_logger("verify_mirror")

async def verify():
    print("🔍 VERIFYING SOS <-> MIRROR CONNECTION")
    print("======================================")

    # 1. REDIS CHECK
    redis_url = os.getenv("SOS_REDIS_URL", "redis://localhost:6379/0")
    print(f"\n[1] Checking Redis ({redis_url})...")
    try:
        r = redis.from_url(redis_url)
        await r.ping()
        print("   ✅ Redis is ALIVE and PONGing.")
    except Exception as e:
        print(f"   ❌ Redis Connection Failed: {e}")

    # 2. LOCAL MEMORY SERVICE (Mirror Port)
    local_mirror_url = "http://localhost:7070"
    print(f"\n[2] Checking Local Mirror Service ({local_mirror_url})...")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{local_mirror_url}/health")
            if resp.status_code == 200:
                print(f"   ✅ Local Mirror is ALIVE: {resp.json()}")
            else:
                print(f"   ⚠️ Local Mirror responded with {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Local Mirror Connection Failed: {e}")
        print("      (Is the docker stack running? 'docker-compose up -d')")

    # 3. PRODUCTION MIRROR (mumega.com)
    prod_mirror_url = "https://mumega.com/mirror"
    print(f"\n[3] Checking Production Mirror ({prod_mirror_url})...")
    try:
        mirror_client = MirrorClient(base_url=prod_mirror_url, agent_id="test_probe")
        # MirrorClient doesn't have a simple public 'ping' without auth usually, 
        # but let's try a health check if the client exposes it or raw request.
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{prod_mirror_url}/health", timeout=5.0)
            if resp.status_code == 200:
                print(f"   ✅ Production Mirror is ALIVE: {resp.text[:100]}")
            else:
                print(f"   ⚠️ Production Mirror responded with {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Production Mirror Connection Failed: {e}")

    # 4. RIVER CONNECTION
    print(f"\n[4] Attempting River Connection...")
    # This simulates what 'connect_river.py' does
    try:
        river_client = MirrorClient(base_url=local_mirror_url, agent_id="river")
        # Try to save a checkpoint
        success = await river_client.save_checkpoint(
            content="River connection verification ping.",
            context={"source": "verification_script"}
        )
        if success:
             print("   ✅ River successfully saved memory to Mirror.")
        else:
             print("   ❌ River failed to save memory.")
    except Exception as e:
        print(f"   ❌ River Connection Logic Failed: {e}")

    print("\n======================================")

if __name__ == "__main__":
    asyncio.run(verify())
