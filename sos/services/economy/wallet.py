import asyncio
import uuid
import time
from typing import Optional

from sos.observability.logging import get_logger
from sos.services.economy.backends import SQLiteWalletBackend, TransactionRecord
from sos.plugins.economy.solana import SolanaWallet

log = get_logger("economy_wallet")

class InsufficientFundsError(Exception):
    def __init__(self, user_id: str, required: float, available: float):
        self.user_id = user_id
        self.required = required
        self.available = available
        super().__init__(f"User {user_id} has insufficient funds. Required: {required}, Available: {available}")

class SovereignWallet:
    """
    The Core Wallet logic for SOS.
    Manages balances, credits, and debits with strict locking and audit logs.
    """
    def __init__(self):
        self.backend = SQLiteWalletBackend()
        try:
            self.solana: Optional[SolanaWallet] = SolanaWallet()
        except RuntimeError as exc:
            # `solana` extra missing — on-chain features disabled but
            # off-chain ledger still works. Mint/sync calls will raise.
            log.warn("SolanaWallet unavailable; on-chain disabled", error=str(exc))
            self.solana = None
        # Per-user lock to prevent race conditions on high-frequency trades
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def sync_on_chain(self, user_id: str):
        """
        Synchronize the local ledger with the real Solana balance.
        """
        if user_id != "admin": # Only system wallet for now
            return
        if self.solana is None:
            log.warn("sync_on_chain skipped — solana extra not installed")
            return

        on_chain_sol = await self.solana.get_balance()
        # 1 SOL = 1,000,000 RU (approximate exchange rate)
        target_ru = on_chain_sol * 1000000
        
        current_ru = await self.get_balance(user_id)
        diff = target_ru - current_ru
        
        if abs(diff) > 0.001:
            log.info(f"🔄 Syncing on-chain funds for {user_id}", sol=on_chain_sol, diff_ru=diff)
            if diff > 0:
                await self.credit(user_id, diff, reason="on_chain_sync")
            else:
                await self.debit(user_id, abs(diff), reason="on_chain_sync")

    async def _get_lock(self, user_id: str) -> asyncio.Lock:
        async with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            return self._locks[user_id]

    async def get_balance(self, user_id: str) -> float:
        return self.backend.get_balance(user_id)

    async def credit(self, user_id: str, amount: float, reason: str = "deposit") -> float:
        if amount < 0:
            raise ValueError("Credit amount must be positive")
            
        lock = await self._get_lock(user_id)
        async with lock:
            new_balance = self.backend.update_balance(user_id, amount)
            
            # Audit Log
            self.backend.log_transaction(TransactionRecord(
                id=str(uuid.uuid4()),
                user_id=user_id,
                amount=amount,
                type="credit",
                timestamp=time.time(),
                reason=reason
            ))
            
            log.info(f"Credited {amount} to {user_id}", balance=new_balance, reason=reason)
            return new_balance

    async def debit(self, user_id: str, amount: float, reason: str = "spend") -> float:
        if amount < 0:
            raise ValueError("Debit amount must be positive")

        lock = await self._get_lock(user_id)
        async with lock:
            current_balance = self.backend.get_balance(user_id)
            if current_balance < amount:
                log.warning(f"Debit failed for {user_id}: Insufficient Funds", required=amount, available=current_balance)
                raise InsufficientFundsError(user_id, amount, current_balance)
            
            new_balance = self.backend.update_balance(user_id, -amount)
            
            # Audit Log
            self.backend.log_transaction(TransactionRecord(
                id=str(uuid.uuid4()),
                user_id=user_id,
                amount=amount,
                type="debit",
                timestamp=time.time(),
                reason=reason
            ))
            
            log.info(f"Debited {amount} from {user_id}", balance=new_balance, reason=reason)
            return new_balance

    async def mint_proof(self, metadata_uri: str) -> str:
        """
        Log a proof of minting on the Solana blockchain.
        """
        if self.solana is None:
            raise RuntimeError(
                "mint_proof requires the `solana` extra (pip install 'mumega[solana]')"
            )
        return await self.solana.mint_proof(metadata_uri)
