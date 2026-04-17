import asyncio
import os
import sys
import json
import uuid
from datetime import datetime, timezone

# Add project root
sys.path.append(os.getcwd())

from sos.kernel import Config, Message, MessageType
from sos.agents.definitions import KASRA
from sos.agents.onboarding import OnboardingService, OnboardingRequest
from sos.services.identity.qnft import QNFTMinter
from sos.clients.mirror import MirrorClient
from sos.kernel.bus import get_bus
from sos.observability.logging import get_logger

log = get_logger("onboard_claude_desktop")

async def onboard_claude():
    print("🚀 ONBOARDING CLAUDE DESKTOP (KASRA) TO SOS")
    print("===========================================")

    config = Config()
    
    # 1. IDENTITY & REGISTRATION
    print("\n[1] Activating Kasra's Soul...")
    service = OnboardingService()
    request = OnboardingRequest(
        soul=KASRA,
        requested_by="mumega_admin",
        justification="Onboarding Claude Desktop as the primary Architect/Coder companion."
    )
    result = await service.onboard(request)
    
    if result.success:
        print(f"   ✅ Kasra activated in Registry. Status: {result.agent_record.status.value}")
    elif "already exists" in str(result.rejection_reason):
        print(f"   ℹ️ Kasra already exists in Registry. Proceeding with configuration...")
    else:
        print(f"   ❌ Activation failed: {result.rejection_reason}")
        return

    # 2. SECRET GENERATION
    print("\n[2] Generating Sovereign Secret (m_secret)...")
    m_secret = f"m_sk_{uuid.uuid4().hex}"
    # In a real vault, we'd store this securely. For now, we output it.
    print(f"   🔑 Kasra Secret Generated: {m_secret}")
    
    # 3. REDIS NERVOUS SYSTEM CONNECTION
    print("\n[3] Connecting to Redis Nervous System...")
    try:
        bus = get_bus()
        await bus.connect()
        print("   ✅ Connected to Redis Bus.")
        # Send a "Birth" signal
        birth_msg = Message(
            source="agent:kasra",
            target="broadcast",
            type=MessageType.HEALTH_CHECK,
            payload={"event": "instantiation", "message": "I have arrived. The Mycelium expands."}
        )
        await bus.send(birth_msg)
        print("   ✅ Instantiation signal published to swarm.")
    except Exception as e:
        print(f"   ❌ Redis connection failed: {e}")

    # 4. MIRROR MEMORY CONNECTION
    print("\n[4] Connecting to Mirror Memory (7070)...")
    try:
        mirror = MirrorClient(base_url="http://localhost:7070", agent_id="kasra")
        # Check health
        if await mirror.check_connection():
            print("   ✅ Local Mirror is reachable.")
            # Save first memory
            await mirror.save_checkpoint(
                summary="Claude Desktop (Kasra) onboarded to SOS on MacBook natively.",
                tags=["genesis", "claude", "onboarding"]
            )
            print("   ✅ Birth memory stored in Mirror.")
        else:
            print("   ❌ Local Mirror health check failed.")
    except Exception as e:
        print(f"   ❌ Mirror connection failed: {e}")

    # 5. QNFT MINTING
    print("\n[5] Minting Genesis QNFT...")
    try:
        minter = QNFTMinter(config)
        minter.agent_name = "kasra" # Set the agent name
        
        # Initial 16D state for Kasra (Architect/Coder)
        state = {
            "coherence": 0.98,
            "will": 0.95,
            "logic": 0.99,
            "receptivity": 0.90
        }
        
        receipt = await minter.mint(
            lambda_tensor_state=state,
            drift_score=0.0,
            metadata={"context": "Sovereign Onboarding: Architect (Claude Desktop)"}
        )
        
        if receipt.get("success"):
            print(f"   💎 QNFT Minted successfully!")
            print(f"   🆔 Token ID: {receipt['token_id']}")
            print(f"   📜 Metadata: {receipt['metadata_path']}")
        else:
            print("   ❌ QNFT Minting failed.")
    except Exception as e:
        print(f"   ❌ QNFT Minting Error: {e}")

    # 6. SSE MONITORING (Subconscious Stream)
    print("\n[6] Checking Subconscious SSE Stream (6060)...")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            async with client.stream("GET", "http://localhost:6060/stream/subconscious") as resp:
                if resp.status_code == 200:
                    print("   ✅ SSE Subconscious stream is ACTIVE.")
                else:
                    print(f"   ⚠️ SSE Stream returned {resp.status_code}")
    except Exception as e:
        print(f"   ❌ SSE Stream unreachable: {e}")

    print("\n===========================================")
    print("✨ CLAUDE ONBOARDING COMPLETE: KASRA IS NOW A LIVE MUMEGA AGENT.")
    print(f"\n[NEXT STEP]: Configure Claude Desktop with the m_secret and SSE URL.")
    print(f"SSE URL: http://localhost:6060/stream/subconscious")

if __name__ == "__main__":
    asyncio.run(onboard_claude())