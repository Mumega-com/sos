import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from sos.observability.logging import get_logger

log = get_logger("solana_plugin")

# On-chain deps live under the `solana` extra. Import lazily so
# `import sos.plugins.economy.solana` works even without the extra
# installed — construction raises a clear error instead.
try:
    import base58  # noqa: F401
    from solana.rpc.async_api import AsyncClient
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.transaction import Transaction
    from solders.system_program import TransferParams, transfer

    _SOLANA_AVAILABLE = True
    _SOLANA_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as _exc:  # pragma: no cover — env-dependent
    _SOLANA_AVAILABLE = False
    _SOLANA_IMPORT_ERROR = _exc
    AsyncClient = Keypair = Pubkey = Transaction = None  # type: ignore[assignment]
    TransferParams = transfer = None  # type: ignore[assignment]

class SolanaWallet:
    """
    Sovereign Solana Wallet for SOS.
    Enables on-chain settlement of RU balances and QNFT proofs.
    """
    def __init__(self, rpc_url: Optional[str] = None, private_key: Optional[str] = None):
        if not _SOLANA_AVAILABLE:
            raise RuntimeError(
                "SolanaWallet requires the `solana` extra "
                "(pip install 'mumega[solana]'). "
                f"Underlying import error: {_SOLANA_IMPORT_ERROR!r}"
            )
        self.rpc_url = rpc_url or os.getenv("SOLANA_RPC_URL", "https://api.devnet.solana.com")
        self.private_key = private_key or os.getenv("SOLANA_PRIVATE_KEY")
        
        self.client = AsyncClient(self.rpc_url)
        self.keypair: Optional[Keypair] = None
        
        if self.private_key:
            try:
                # Support both base58 and raw byte arrays (as JSON)
                if self.private_key.startswith("["):
                    import json
                    self.keypair = Keypair.from_bytes(json.loads(self.private_key))
                else:
                    self.keypair = Keypair.from_base58_string(self.private_key)
            except Exception as e:
                log.error("Failed to load Solana keypair", error=str(e))
        
        if not self.keypair:
            # Generate a temporary ephemeral key for testing/dev
            self.keypair = Keypair()
            log.warn("Using ephemeral Solana keypair (NOT PERSISTENT)")
            
        log.info(f"Solana Wallet Initialized: {self.keypair.pubkey()}")

    async def get_balance(self) -> float:
        """Fetch balance in SOL"""
        if not self.keypair:
            return 0.0
        
        try:
            resp = await self.client.get_balance(self.keypair.pubkey())
            return resp.value / 1e9
        except Exception as e:
            log.error("Solana balance check failed", error=str(e))
            return 0.0

    async def transfer(self, to_pubkey: str, amount_sol: float) -> str:
        """
        Execute an on-chain transfer.
        """
        if not self.keypair:
            raise RuntimeError("Wallet not configured for transactions.")
            
        try:
            target = Pubkey.from_string(to_pubkey)
            lamports = int(amount_sol * 1e9)
            
            ix = transfer(
                TransferParams(
                    from_pubkey=self.keypair.pubkey(),
                    to_pubkey=target,
                    lamports=lamports
                )
            )
            
            recent_blockhash = (await self.client.get_latest_blockhash()).value.blockhash
            
            tx = Transaction.new_signed_with_payer(
                [ix],
                self.keypair.pubkey(),
                [self.keypair],
                recent_blockhash
            )
            
            log.info(f"Sending {amount_sol} SOL tx to {to_pubkey}...")
            resp = await self.client.send_transaction(tx)
            return str(resp.value)
            
        except Exception as e:
            log.error("Transfer failed", error=str(e))
            raise

    async def mint_proof(self, metadata_uri: str) -> str:
        """
        Log a 'Proof of Existence' for a QNFT on-chain.
        Currently implemented as a micro-transfer to self.
        """
        # In a real scenario, we'd use the Memo Program. 
        # For Phase 1, a micro-transfer to self creates an on-chain event.
        log.info(f"Logging QNFT Proof for URI: {metadata_uri}")
        return await self.transfer(str(self.keypair.pubkey()), 0.000001)

    async def close(self):
        await self.client.close()
