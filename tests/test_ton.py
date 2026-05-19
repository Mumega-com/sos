
import asyncio
from sos.plugins.economy.ton import TonWallet

async def test_ton_plugin():
    print("--- Testing TON Wallet Plugin ---")
    
    # 1. Initialize
    wallet = TonWallet()
    await wallet.initialize()
    
    # 2. Check Balance
    print("\n[Action] Checking Balance...")
    balance = await wallet.get_balance("EQD4FPq-PRDieyQKkizFTRtSDyjv9wAtj2V7Q6_H_s5_MIND")
    print(f" > Balance: {balance}")
    
    # 3. Transfer TON
    print("\n[Action] Transferring 5 TON...")
    tx = await wallet.transfer(
        to_address="EQBvW8Z5huBkMJYdnfAqw5PjQv_1GqX0t8_7L8L8L8L8L8L8",
        amount=5.0,
        token="TON"
    )
    print(f" > TX Result: {tx}")

    # 4. Transfer Jetton (MIND)
    print("\n[Action] Transferring 100 MIND...")
    tx_jetton = await wallet.transfer(
        to_address="EQBvW8Z5huBkMJYdnfAqw5PjQv_1GqX0t8_7L8L8L8L8L8L8",
        amount=100.0,
        token="MIND"
    )
    print(f" > Jetton TX Result: {tx_jetton}")
    
    await wallet.close()
    print("\n✅ TON Plugin Test Complete.")

if __name__ == "__main__":
    import os
    # Mock Env for test
    os.environ["SOS_TON_MNEMONIC"] = "word1 word2 ..." 
    asyncio.run(test_ton_plugin())
