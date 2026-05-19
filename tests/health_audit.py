
import asyncio
import httpx
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

async def health_check():
    print("="*60)
    print("🛡️ SOS SYSTEM HEALTH AUDIT")
    print("="*60)
    
    services = {
        "Engine (8000)": "http://localhost:8000/health",
        "Identity (8004)": "http://localhost:8004/health",
        "Static Deck (4388)": "http://localhost:4388/",
        "Redis Bus (6379)": "redis"
    }
    
    async with httpx.AsyncClient() as client:
        for name, url in services.items():
            try:
                if url == "redis":
                    import redis.asyncio as redis
                    r = redis.from_url("redis://localhost:6379/0")
                    await r.ping()
                    print(f"🟢 {name}: CONNECTED")
                else:
                    resp = await client.get(url, timeout=2.0)
                    if resp.status_code == 200:
                        print(f"🟢 {name}: ONLINE (200 OK)")
                    else:
                        print(f"🔴 {name}: DEGRADED ({resp.status_code})")
            except Exception as e:
                print(f"🔴 {name}: OFFLINE ({type(e).__name__})")

    print("\n" + "="*60)
    print("✅ AUDIT COMPLETE")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(health_check())
