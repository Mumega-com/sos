import os
import json
import logging
import asyncio
from typing import Optional, Dict, List, Any
from pathlib import Path
from datetime import datetime

from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.message import Message
from solders.system_program import TransferParams, transfer
from spl.token.instructions import get_associated_token_address, transfer_checked, TransferCheckedParams

from sovereign.config import get_path_config

logger = logging.getLogger(__name__)

# Constants
MIND_TOKEN_MINT = "MINDmJpX1n2j3k4l5m6n7o8p9q0r1s2t3u4v5w6x7y8"  # Placeholder/Devnet
NETWORK_RPC_URLS = {
    "devnet": "https://api.devnet.solana.com",
    "mainnet": "https://api.mainnet-beta.solana.com",
    "localnet": "http://127.0.0.1:8899",
}

def get_rpc_url(network: str) -> str:
    return NETWORK_RPC_URLS.get(network, NETWORK_RPC_URLS["devnet"])

class TreasuryWallet:
    # Witness approval threshold for high-value payouts
    WITNESS_APPROVAL_THRESHOLD = 100.0  # $MIND - payouts above this require Rider approval

    def __init__(self, agent_name: str = "Antigravity", network: Optional[str] = None):
        self.agent_name = agent_name
        # Use provided network or get from environment
        self.network = network or os.getenv("SOLANA_NETWORK", "devnet")
        self.rpc_url = get_rpc_url(self.network)
        
        self.key_path = Path(os.path.expanduser("~/.config/solana/id.json"))
        paths = get_path_config()
        self.token_info_path = paths.mind_token_path
        self.saga_path = paths.saga_path
        self.approvals_path = paths.data_dir / "treasury_approvals.json"

        self._payer: Optional[Keypair] = None
        self._mint: Optional[Pubkey] = None
        self._decimals = 9
        self._mainnet_confirmation_enabled = True  # Safety flag for mainnet transactions
        self._pending_approvals: Dict[str, Dict] = {}  # work_id -> payout details

        self.load_credentials()
        self.load_approvals()

        if self.network == "mainnet":
            logger.warning(f"⚠️ TREASURY CONNECTED TO MAINNET ({self.rpc_url}) ⚠️")
        else:
            logger.info(f"Treasury connected to {self.network} at {self.rpc_url}")


    def load_credentials(self):
        """Load wallet and token info"""
        if not self.key_path.exists():
            logger.warning(f"No wallet found at {self.key_path}. Treasury is read-only or disabled.")
            return

        try:
            with open(self.key_path, 'r') as f:
                key_data = json.load(f)
            self._payer = Keypair.from_bytes(key_data)
        except Exception as e:
            logger.error(f"Failed to load wallet: {e}")

        # Load token mint info if available
        if self.token_info_path.exists():
            try:
                with open(self.token_info_path, 'r') as f:
                    info = json.load(f)
                    # Support both new 'mint_address' and legacy 'address' keys
                    mint_str = info.get("mint_address") or info.get("address") or MIND_TOKEN_MINT
                    self._mint = Pubkey.from_string(mint_str)
                    self._decimals = info.get("decimals", 9)
            except Exception as e:
                logger.error(f"Failed to load token info: {e}")
        else:
            # Fallback
            try:
                self._mint = Pubkey.from_string(MIND_TOKEN_MINT)
            except:
                pass

    def load_approvals(self):
        """Load pending approvals from disk persistence."""
        if self.approvals_path.exists():
            try:
                with open(self.approvals_path, 'r') as f:
                    self._pending_approvals = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load approvals: {e}")
                self._pending_approvals = {}

    def save_approvals(self):
        """Save pending approvals to disk persistence."""
        try:
            with open(self.approvals_path, 'w') as f:
                json.dump(self._pending_approvals, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save approvals: {e}")

    async def get_balance(self) -> Dict[str, Any]:
        """Get full treasury balance (SOL + MIND)"""
        if not self._payer:
            return {"SOL": 0.0, "MIND": 0.0, "address": None}
        
        address = str(self._payer.pubkey())
        return await self.get_account_balance(address)

    async def get_account_balance(self, address: str) -> Dict[str, Any]:
        """Get SOL and MIND balance for any address"""
        sol_bal = 0.0
        mind_bal = 0.0
        
        async with AsyncClient(self.rpc_url) as client:
            try:
                pubkey = Pubkey.from_string(address)
                resp = await client.get_balance(pubkey)
                sol_bal = resp.value / 10**9
                if self._mint:
                    ata = get_associated_token_address(pubkey, self._mint)
                    try:
                        token_resp = await client.get_token_account_balance(ata)
                        mind_bal = float(token_resp.value.ui_amount_string)
                    except Exception:
                        mind_bal = 0.0
            except Exception as e:
                logger.warning(f"Balance check failed for {address}: {e}")
        return {"SOL": sol_bal, "MIND": mind_bal, "address": address, "network": self.network}

    async def get_token_balance(self) -> float:
        """Get MIND token balance"""
        if not self._payer or not self._mint:
            return 0.0

        async with AsyncClient(self.rpc_url) as client:
            try:
                # Get associated token account
                ata = get_associated_token_address(self._payer.pubkey(), self._mint)
                resp = await client.get_token_account_balance(ata)
                return float(resp.value.ui_amount_string)
            except Exception as e:
                logger.warning(f"Could not fetch token balance: {e}")
                return 0.0

    def disable_mainnet_safety(self):
        """Allow mainnet transactions (DANGER)"""
        logger.warning("🚨 MAINNET SAFETY CHECKS DISABLED. REAL FUNDS AT RISK. 🚨")
        self._mainnet_confirmation_enabled = False

    def enable_mainnet_safety(self):
        logger.info("Treasury mainnet safety checks re-enabled.")
        self._mainnet_confirmation_enabled = True

    def _log_saga(self, event_type: str, details: Dict[str, Any]) -> None:
        """Log treasury events to the Sovereign Saga for audit trail."""
        timestamp = datetime.now().isoformat()
        
        icon = "💰"
        if "approve" in event_type.lower():
            icon = "✅"
        elif "reject" in event_type.lower():
            icon = "❌"
        elif "pending" in event_type.lower():
            icon = "⏳"
            
        entry = f"""
### {icon} Treasury: {event_type}
*Time: {timestamp}*

> {details.get('message', 'Treasury action occurred.')}

**Details:**
- Amount: `{details.get('amount', 0)} $MIND`
- Recipient: `{details.get('recipient', 'Unknown')}`
- Reason: `{details.get('reason', 'N/A')}`
"""
        
        try:
            mode = 'a' if self.saga_path.exists() else 'w'
            with open(self.saga_path, mode) as f:
                if mode == 'w':
                    f.write("# The Sovereign Saga\n*The Living History of the Swarm*\n\n")
                f.write(entry)
            logger.debug(f"📜 Saga logged: {event_type}")
        except Exception as e:
            logger.warning(f"Failed to log to Saga: {e}")

    def request_approval(
        self,
        work_id: str,
        recipient_addr: str,
        amount: float,
        reason: str = "Bounty"
    ) -> Dict[str, Any]:
        """
        Request Rider (Witness) approval for a high-value payout.
        Returns approval request details. Payout must be explicitly approved.
        """
        approval_request = {
            "work_id": work_id,
            "recipient": recipient_addr,
            "amount": amount,
            "reason": reason,
            "requested_at": datetime.now().isoformat(),
            "status": "pending",
        }
        
        self._pending_approvals[work_id] = approval_request
        self.save_approvals()  # Persist state
        
        self._log_saga("Payout Pending Approval", {
            "message": f"High-value payout of {amount} $MIND requires Rider approval.",
            "amount": amount,
            "recipient": recipient_addr,
            "reason": reason,
        })
        
        logger.info(f"⏳ Payout pending approval: {amount} $MIND for work {work_id}")
        return approval_request

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        """List all pending payout approval requests."""
        self.load_approvals() # Refresh from disk
        return list(self._pending_approvals.values())

    async def approve_payout(
        self,
        work_id: str,
        witness_id: str = "rider",
        force: bool = False
    ) -> str:
        """
        Approve a pending payout and execute the transaction.
        """
        self.load_approvals() # Refresh from disk
        
        if work_id not in self._pending_approvals:
            raise ValueError(f"No pending approval found for work {work_id}")
        
        approval = self._pending_approvals[work_id]
        
        self._log_saga("Payout Approved", {
            "message": f"Rider {witness_id} approved payout.",
            "amount": approval["amount"],
            "recipient": approval["recipient"],
            "reason": approval["reason"],
        })
        
        # Execute the payout
        tx_sig = await self.pay_bounty(
            recipient_addr=approval["recipient"],
            amount=approval["amount"],
            reason=f"{approval['reason']} (Approved by {witness_id})",
            force=force,
        )
        
        # Remove from pending and save
        del self._pending_approvals[work_id]
        self.save_approvals()
        
        return tx_sig

    def reject_payout(self, work_id: str, witness_id: str = "rider", reason: str = "") -> bool:
        """Reject a pending payout request."""
        self.load_approvals()
        
        if work_id not in self._pending_approvals:
            return False
        
        approval = self._pending_approvals[work_id]
        
        self._log_saga("Payout Rejected", {
            "message": f"Rider {witness_id} rejected payout. Reason: {reason or 'Not specified'}",
            "amount": approval["amount"],
            "recipient": approval["recipient"],
            "reason": reason,
        })
        
        del self._pending_approvals[work_id]
        self.save_approvals()
        
        logger.info(f"❌ Payout rejected for work {work_id}")
        return True

    async def pay_bounty_with_witness(
        self,
        work_id: str,
        recipient_addr: str,
        amount: float,
        reason: str = "Bounty",
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Pay a bounty, routing through witness approval if above threshold.
        """
        # Check if witness approval is required
        if amount >= self.WITNESS_APPROVAL_THRESHOLD and not force:
            approval = self.request_approval(work_id, recipient_addr, amount, reason)
            return {
                "status": "pending_approval",
                "message": f"Payout of {amount} $MIND requires Rider approval.",
                "approval": approval,
            }
        
        # Execute payout directly
        result = await self.pay_bounty(recipient_addr, amount, reason, force)
        
        if result.get("status") != "success":
            return result

        tx_sig = result["tx_signature"]
        
        self._log_saga("Payout Executed", {
            "message": f"Direct payout of {amount} $MIND completed.",
            "amount": amount,
            "recipient": recipient_addr,
            "reason": reason,
        })
        
        return {
            "status": "paid",
            "tx_signature": tx_sig,
            "amount": amount,
            "recipient": recipient_addr,
        }

    async def pay_bounty(
        self,
        recipient_addr: str,
        amount: float,
        reason: str = "Bounty",
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Send MIND tokens to a recipient.
        """
        if not self._payer or not self._mint:
            raise ValueError("Treasury wallet or token mint not configured.")

        if self.network == "mainnet" and self._mainnet_confirmation_enabled and not force:
            logger.warning(f"🛑 Safety Block: Mainnet payout of {amount} to {recipient_addr} blocked.")
            return {
                "status": "blocked", 
                "reason": "mainnet_safety_lock",
                "detail": "Mainnet safety lock active. Use force=True or disable safety."
            }

        async with AsyncClient(self.rpc_url) as client:
            try:
                recipient_pubkey = Pubkey.from_string(recipient_addr)
                
                # Get sender ATA
                sender_ata = get_associated_token_address(self._payer.pubkey(), self._mint)
                
                # Get or create recipient ATA (simplified: assuming it exists or we use transfer_checked logic slightly differently)
                # For robustness, we usually check if recipient ATA exists. If not, we might need to create it.
                # Here we assume recipient has an ATA or we use a transfer instruction that handles it (like SPL Token 2022)
                # But standard SPL requires ATA.
                
                recipient_ata = get_associated_token_address(recipient_pubkey, self._mint)
                
                # Amount in base units
                amount_base = int(amount * (10 ** self._decimals))

                logger.info(f"Paying {amount} MIND to {recipient_addr}...")

                # Construct transaction
                # Note: In a real implementation, we'd check if recipient_ata exists and add create_associated_token_account instruction if needed.
                # For this prototype, we'll assume it exists or fail.
                
                ix = transfer_checked(
                    TransferCheckedParams(
                        program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
                        source=sender_ata,
                        mint=self._mint,
                        dest=recipient_ata,
                        owner=self._payer.pubkey(),
                        amount=amount_base,
                        decimals=self._decimals,
                        signers=[self._payer.pubkey()]
                    )
                )
                
                recent_blockhash = await client.get_latest_blockhash()
                txn = Transaction(recent_blockhash=recent_blockhash.value.blockhash, fee_payer=self._payer.pubkey(), instructions=[ix])
                txn.sign([self._payer])
                
                resp = await client.send_transaction(txn)
                tx_sig = str(resp.value)
                logger.info(f"Payment sent! Signature: {tx_sig}")

                # TD-018: Confirm transaction on-chain before returning
                try:
                    confirmation = await client.confirm_transaction(
                        tx_sig,
                        commitment="confirmed"
                    )
                    if confirmation.value and confirmation.value[0]:
                        if confirmation.value[0].err:
                            logger.error(f"Transaction failed on-chain: {confirmation.value[0].err}")
                            raise RuntimeError(f"Transaction failed: {confirmation.value[0].err}")
                    logger.info(f"✅ Transaction confirmed: {tx_sig}")
                except asyncio.TimeoutError:
                    logger.warning(f"⚠️ Transaction confirmation timed out for {tx_sig} - may still succeed")

                return {"status": "success", "tx_signature": tx_sig}

            except Exception as e:
                logger.error(f"Payment failed: {e}")
                return {"status": "failed", "error": str(e)}

    async def verify_transaction(self, tx_signature: str) -> Dict[str, Any]:
        """
        Verify a transaction exists and was successful on-chain.

        TD-018: Post-hoc verification for auditing and recovery.

        Args:
            tx_signature: The transaction signature to verify

        Returns:
            Dict with status, confirmations, and error info if any
        """
        async with AsyncClient(self.rpc_url) as client:
            try:
                result = await client.get_signature_statuses([tx_signature])

                if not result.value or not result.value[0]:
                    return {
                        "status": "not_found",
                        "signature": tx_signature,
                        "message": "Transaction not found on-chain"
                    }

                status = result.value[0]
                if status.err:
                    return {
                        "status": "failed",
                        "signature": tx_signature,
                        "error": str(status.err),
                        "confirmations": status.confirmations
                    }

                return {
                    "status": "confirmed",
                    "signature": tx_signature,
                    "confirmations": status.confirmations,
                    "slot": status.slot
                }

            except Exception as e:
                logger.error(f"Failed to verify transaction {tx_signature}: {e}")
                return {
                    "status": "error",
                    "signature": tx_signature,
                    "error": str(e)
                }
