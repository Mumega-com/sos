import asyncio
import shutil
from pathlib import Path
from sos.kernel import Config
from sos.services.identity.core import IdentityCore

async def main():
    print("💎 Testing QNFT Minting Service...")
    
    # 1. Setup Config (mock data dir)
    config = Config()
    config.data_dir = "test_data_sos"
    
    # Clean previous run
    p = Path(config.data_dir)
    if p.exists():
        shutil.rmtree(p)
    p.mkdir()
    
    # 2. Init Identity Core
    print("✅ Initializing Identity Core...")
    identity = IdentityCore(config)
    
    # 3. Trigger Mint
    print("🚀 Triggering Mint...")
    pass_obj = await identity.mint_guild_pass(
        agent_id="test_agent_001",
        role="Technomancer",
        edition="Founders"
    )
    
    # 4. Verify Receipt
    print(f"✅ Mint Receipt: {pass_obj.token_id}")
    print(f"📜 Metadata Receipt: {pass_obj.metadata}")
    
    # 5. Verify File on Disk
    receipt = pass_obj.metadata["receipt"]
    # Verify metadata_path matches
    real_path = Path(receipt["metadata_path"])
    if real_path.exists():
        print(f"✅ Metadata file exists at: {real_path}")
        with open(real_path) as f:
            print(f"📄 Content Preview: {f.read()[:100]}...")
    else:
        print(f"❌ Metadata file MISSING at {real_path}")

if __name__ == "__main__":
    asyncio.run(main())
