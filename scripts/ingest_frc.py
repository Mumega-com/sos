
import asyncio
import os
import sys
from pathlib import Path
import httpx

# SOS standardized port for Mirror
MIRROR_URL = "http://localhost:7070"

async def ingest_frc_library():
    print("📚 Starting FRC Library Ingestion into Mirror...")
    
    # Actual paths found via find
    _home = Path.home()
    frc_paths = [
        _home / "infra/shared-kb/Books/FRC TEXT Book v.1 ECR.md",
        _home / "infra/shared-kb/frc/830_series/FRC_830_501_The_Master_Plan.md",
        _home / "infra/shared-kb/frc/830_series/FRC_830_505_Economics.md",
        _home / "infra/shared-kb/frc/830_series/FRC_830_503_The_Vault.md",
        _home / "torivers/papers/FRC.100.001.md",
        _home / "torivers/papers/FRC.100.003.md",
        _home / "torivers/papers/FRC.566.001.md",
        _home / "cli_old/docs/FRC_ARF_FORMULA.md",
        _home / "cli_old/docs/FRC_LAMBDA_TENSOR.md",
        _home / "mirror/Archive/Artifacts/FRC_841_004_CGL.md",
    ]
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for path in frc_paths:
            if not path.exists():
                print(f"⚠️ Could not find {path}")
                continue
                
            content = path.read_text()
            print(f"📖 Ingesting {path.name} ({len(content)} chars)...")
            
            payload = {
                "content": content,
                "metadata": {
                    "source": "frc_library",
                    "filename": path.name,
                    "type": "physics_base",
                    "path": str(path)
                }
            }
            
            try:
                resp = await client.post(f"{MIRROR_URL}/add", json=payload)
                if resp.status_code == 200:
                    print(f"✅ Stored {path.name} in Mirror.")
                else:
                    print(f"❌ Failed to store {path.name}: {resp.status_code}")
            except Exception as e:
                print(f"❌ Error connecting to Mirror for {path.name}: {e}")

if __name__ == "__main__":
    asyncio.run(ingest_frc_library())
