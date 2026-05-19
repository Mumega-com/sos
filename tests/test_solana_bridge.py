import asyncio
import os
import sys
from sos.clients.economy import EconomyClient

async def main():
    # Note: Ensure SOLANA_PRIVATE_KEY is set in environment for this to work
    # Or it will use the default/ephemeral key which might have 0 balance.
    
    config_url = "http://localhost:8002"
    client = EconomyClient(config_url)
    
    print("⛓️ Testing Solana Bridge (Economy Service)...")
    
    try:
        # 1. Health Check
        health = client.health()
        print(f"Health: {health.get('status')}")
        
        # 2. Balance Check
        # Using 'admin' as it maps to the system wallet in SovereignWallet
        balance = await client.get_balance("admin")
        print(f"💰 Admin Wallet Balance (On-Chain): {balance} SOL")
        
        # 3. Mint Proof (Self-transfer)
        print("🚀 Sending On-Chain Proof (Memo/Micro-transfer)...")
        proof = await client.mint_proof("test_metadata_uri_v1")
        
        print("\n✅ Solana Transaction Confirmed!")
        print(f"Signature: {proof.get('signature')}")
        print(f"Explorer: https://explorer.solana.com/tx/{proof.get('signature')}?cluster=devnet")
        
    except Exception as e:
        print(f"❌ Solana Bridge Test Failed: {e}")
        if "Connection refused" in str(e):
            print("💡 Tip: Is the swarm running? (./boot_swarm.sh)")

if __name__ == "__main__":
    asyncio.run(main())
