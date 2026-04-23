
import json
import logging
import os
import uuid
import asyncio
from typing import List, Dict, Optional, Callable
from pathlib import Path
from datetime import datetime, timedelta

from sovereign.treasury import TreasuryWallet
from sovereign.config import get_path_config
from enum import Enum
class BountyStatus(Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    PAID = "paid"
    COMPLETED = "completed"
    EXPIRED = "expired"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    DISPUTED = "disputed"

logger = logging.getLogger("bounty_board")

# Default expiration check interval (seconds)
DEFAULT_EXPIRATION_CHECK_INTERVAL = 300  # 5 minutes

class BountyBoard:
    def __init__(self, agent_name: str = "Antigravity"):
        self.agent_name = agent_name
        paths = get_path_config()
        self.bounty_dir = paths.bounties_dir
        self.bounty_dir.mkdir(parents=True, exist_ok=True)
        self.treasury = TreasuryWallet(agent_name)

        # Background expiration watcher
        self._watcher_task: Optional[asyncio.Task] = None
        self._watcher_running = False

    def _get_path(self, bounty_id: str) -> Path:
        return self.bounty_dir / f"{bounty_id}.json"

    async def post_bounty(
        self,
        title: str,
        description: str,
        reward: float,
        constraints: List[str] = [],
        timeout_hours: float = 48.0,
        creator_wallet: Optional[str] = None
    ) -> str:
        """Create a new Bounty

        Args:
            title: Bounty title
            description: Detailed description of work required
            reward: Amount in MIND tokens
            constraints: List of constraint strings
            timeout_hours: Hours until bounty expires after being claimed (default 48)
            creator_wallet: Wallet address for refund if bounty expires (optional)
        """
        bounty_id = str(uuid.uuid4())[:8]
        created_at = datetime.now()
        expires_at = created_at + timedelta(hours=timeout_hours)

        bounty = {
            "id": bounty_id,
            "title": title,
            "description": description,
            "reward": reward,
            "currency": "MIND",
            "status": BountyStatus.OPEN.value,
            "constraints": constraints,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "timeout_hours": timeout_hours,
            "creator_wallet": creator_wallet,
            "claimant": None,
            "submission": None
        }
        
        with open(self._get_path(bounty_id), "w") as f:
            json.dump(bounty, f, indent=2)
            
        logger.info(f"📜 Bounty Posted: {title} ({reward} MIND) [ID: {bounty_id}]")
        return bounty_id

    async def expire_stale_bounties(self) -> List[str]:
        """Mark CLAIMED bounties as EXPIRED if not submitted within timeout.

        Returns:
            List of bounty IDs that were expired
        """
        expired_ids = []
        now = datetime.now()

        for fpath in self.bounty_dir.glob("*.json"):
            try:
                with open(fpath, "r+") as f:
                    data = json.load(f)

                    # Only expire CLAIMED bounties that have passed their expiration
                    if data["status"] != BountyStatus.CLAIMED.value:
                        continue

                    # Check if expires_at exists (for backwards compatibility)
                    if "expires_at" not in data:
                        continue

                    expires_at = datetime.fromisoformat(data["expires_at"])
                    if now > expires_at:
                        data["status"] = BountyStatus.EXPIRED.value
                        data["expired_at"] = now.isoformat()

                        f.seek(0)
                        json.dump(data, f, indent=2)
                        f.truncate()

                        expired_ids.append(data["id"])
                        logger.warning(f"⏰ Bounty {data['id']} expired: {data['title']}")

            except Exception as e:
                logger.error(f"Error checking bounty expiration for {fpath}: {e}")
                continue

        return expired_ids

    async def list_bounties(self, status: str = "OPEN") -> List[Dict]:
        """List bounties by status.

        Automatically expires stale CLAIMED bounties before listing.
        """
        # Auto-expire stale bounties before listing
        await self.expire_stale_bounties()

        bounties = []
        for fpath in self.bounty_dir.glob("*.json"):
            try:
                with open(fpath) as f:
                    data = json.load(f)
                    if status == "ALL" or data["status"] == status:
                        bounties.append(data)
            except Exception:
                continue
        return bounties

    async def claim_bounty(self, bounty_id: str, agent_address: str) -> bool:
        """Claim a bounty for work"""
        path = self._get_path(bounty_id)
        if not path.exists():
            return False
            
        with open(path, "r+") as f:
            data = json.load(f)
            if data["status"] != BountyStatus.OPEN.value:
                logger.warning(f"Bounty {bounty_id} is not OPEN.")
                return False

            data["status"] = BountyStatus.CLAIMED.value
            data["claimant"] = agent_address
            data["claimed_at"] = datetime.now().isoformat()
            
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()
            
        logger.info(f"👷 Bounty {bounty_id} claimed by {agent_address[:8]}...")
        return True

    async def submit_solution(self, bounty_id: str, proof_url: str) -> bool:
        """Submit proof of work"""
        path = self._get_path(bounty_id)
        if not path.exists():
            return False
            
        with open(path, "r+") as f:
            data = json.load(f)
            if data["status"] != BountyStatus.CLAIMED.value:
                return False

            data["status"] = BountyStatus.SUBMITTED.value
            data["submission"] = proof_url
            data["submitted_at"] = datetime.now().isoformat()
            
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()
        
        logger.info(f"📝 Solution submitted for {bounty_id}: {proof_url}")
        return True

    async def approve_and_pay(self, bounty_id: str) -> str:
        """Verify (human-in-the-loop or auto) and Pay"""
        path = self._get_path(bounty_id)
        if not path.exists():
            return "Failed: Not Found"
            
        data = {}
        with open(path) as f:
            data = json.load(f)
            
        if data["status"] != BountyStatus.SUBMITTED.value:
            return "Failed: Not in SUBMITTED state"

        recipient = data["claimant"]
        amount = data["reward"]

        logger.info(f"🧐 Verifying Bounty {bounty_id}...")

        # In a real system, we would run tests here.
        # For now, we assume implicit approval if this method is called.

        try:
            payout = await self.treasury.pay_bounty_with_witness(
                bounty_id,
                recipient,
                amount,
                reason=f"Bounty {bounty_id} Reward",
            )

            if isinstance(payout, dict):
                payout_status = payout.get("status")
                if payout_status == "pending_approval":
                    data["status"] = BountyStatus.APPROVED.value
                    data["payment_status"] = "pending_approval"
                    data["approval"] = payout.get("approval")
                    data["approval_requested_at"] = datetime.now().isoformat()
                    message = "Pending: Awaiting witness approval"
                elif payout_status == "blocked":
                    data["payment_status"] = "blocked"
                    data["payment_error"] = payout.get("reason") or "blocked"
                    message = "Failed: Blocked by safety check"
                elif payout_status == "failed":
                    data["payment_status"] = "failed"
                    data["payment_error"] = payout.get("error") or "unknown"
                    message = f"Error: {data['payment_error']}"
                elif payout_status in ["paid", "success"]:
                    data["status"] = BountyStatus.PAID.value
                    data["payment_status"] = "paid"
                    data["tx_signature"] = payout.get("tx_signature")
                    data["paid_at"] = datetime.now().isoformat()
                    message = f"Success: Paid {amount} MIND. Tx: {data['tx_signature']}"
                else:
                    data["payment_status"] = "failed"
                    data["payment_error"] = f"unknown_status:{payout_status}"
                    message = "Error: Unknown payout status"
            else:
                tx = payout
                if tx == "blocked_by_safety_check":
                    data["payment_status"] = "blocked"
                    data["payment_error"] = "blocked_by_safety_check"
                    message = "Failed: Blocked by safety check"
                else:
                    data["status"] = BountyStatus.PAID.value
                    data["payment_status"] = "paid"
                    data["tx_signature"] = tx
                    data["paid_at"] = datetime.now().isoformat()
                    message = f"Success: Paid {amount} MIND. Tx: {tx}"

            with open(path, "r+") as f:
                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()

            return message

        except Exception as e:
            logger.error(f"Payout failed: {e}")
            return f"Error: {str(e)}"

    async def refund_expired_bounty(self, bounty_id: str, creator_wallet: Optional[str] = None) -> Dict:
        """Refund an expired bounty to its creator.

        TD-006: Expired bounties should return funds to the creator.

        Args:
            bounty_id: The bounty to refund
            creator_wallet: Wallet address to refund to (uses bounty creator if not provided)

        Returns:
            Dict with status and transaction details
        """
        path = self._get_path(bounty_id)
        if not path.exists():
            return {"status": "error", "message": "Bounty not found"}

        with open(path, "r+") as f:
            data = json.load(f)

            if data["status"] != BountyStatus.EXPIRED.value:
                return {"status": "error", "message": f"Bounty is {data['status']}, not EXPIRED"}

            # Get refund recipient (creator or provided wallet)
            recipient = creator_wallet or data.get("creator_wallet")
            if not recipient:
                # Mark as refund pending - no wallet to refund to
                data["status"] = BountyStatus.REFUND_PENDING.value
                data["refund_pending_at"] = datetime.now().isoformat()
                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()
                return {
                    "status": "pending",
                    "message": "No creator wallet - marked as REFUND_PENDING"
                }

            amount = data["reward"]

            try:
                payout = await self.treasury.pay_bounty_with_witness(
                    bounty_id,
                    recipient,
                    amount,
                    reason=f"Bounty {bounty_id} refund (expired)"
                )

                if isinstance(payout, dict):
                    payout_status = payout.get("status")
                    if payout_status == "pending_approval":
                        data["status"] = BountyStatus.REFUND_PENDING.value
                        data["refund_status"] = "pending_approval"
                        data["refund_approval"] = payout.get("approval")
                        data["refund_pending_at"] = datetime.now().isoformat()
                        message = "Refund pending approval"
                        response_status = "pending"
                    elif payout_status == "blocked":
                        data["refund_status"] = "blocked"
                        data["refund_error"] = payout.get("reason") or "blocked"
                        message = "Refund blocked by safety check"
                        response_status = "blocked"
                    elif payout_status == "failed":
                        data["refund_status"] = "failed"
                        data["refund_error"] = payout.get("error") or "unknown"
                        message = data["refund_error"]
                        response_status = "error"
                    elif payout_status in ["paid", "success"]:
                        data["status"] = BountyStatus.REFUNDED.value
                        data["refund_status"] = "paid"
                        data["refund_tx"] = payout.get("tx_signature")
                        data["refunded_at"] = datetime.now().isoformat()
                        data["refund_recipient"] = recipient
                        message = "Refunded"
                        response_status = "success"
                    else:
                        data["refund_status"] = "failed"
                        data["refund_error"] = f"unknown_status:{payout_status}"
                        message = "Unknown payout status"
                        response_status = "error"
                    tx = data.get("refund_tx")
                else:
                    tx = payout
                    if tx == "blocked_by_safety_check":
                        data["refund_status"] = "blocked"
                        data["refund_error"] = "blocked_by_safety_check"
                        message = "Refund blocked by safety check"
                        response_status = "blocked"
                    else:
                        data["status"] = BountyStatus.REFUNDED.value
                        data["refund_status"] = "paid"
                        data["refund_tx"] = tx
                        data["refunded_at"] = datetime.now().isoformat()
                        data["refund_recipient"] = recipient
                        message = "Refunded"
                        response_status = "success"

                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()

                if response_status == "success":
                    logger.info(f"💸 Bounty {bounty_id} refunded {amount} MIND to {recipient[:8]}...")
                return {
                    "status": response_status,
                    "message": message,
                    "amount": amount,
                    "tx": tx,
                    "recipient": recipient
                }

            except Exception as e:
                logger.error(f"Refund failed for bounty {bounty_id}: {e}")
                return {"status": "error", "message": str(e)}

    async def start_expiration_watcher(
        self,
        interval: int = DEFAULT_EXPIRATION_CHECK_INTERVAL,
        auto_refund: bool = True,
        on_expire: Optional[Callable[[str, Dict], None]] = None
    ):
        """Start background task to auto-expire stale bounties.

        TD-005: Automatic bounty expiration without manual intervention.

        Args:
            interval: Seconds between expiration checks (default 5 minutes)
            auto_refund: Automatically refund expired bounties if creator wallet known
            on_expire: Optional callback for each expired bounty (bounty_id, bounty_data)
        """
        if self._watcher_running:
            logger.warning("Expiration watcher already running")
            return

        self._watcher_running = True
        logger.info(f"🕐 Bounty expiration watcher started (interval: {interval}s)")

        async def watcher_loop():
            while self._watcher_running:
                try:
                    expired_ids = await self.expire_stale_bounties()

                    for bounty_id in expired_ids:
                        # Load bounty data for callback
                        bounty_data = None
                        path = self._get_path(bounty_id)
                        if path.exists():
                            with open(path) as f:
                                bounty_data = json.load(f)

                        # Call custom callback if provided
                        if on_expire and bounty_data:
                            try:
                                on_expire(bounty_id, bounty_data)
                            except Exception as e:
                                logger.error(f"on_expire callback failed: {e}")

                        # Auto-refund if enabled and creator wallet known
                        if auto_refund and bounty_data:
                            creator_wallet = bounty_data.get("creator_wallet")
                            if creator_wallet:
                                result = await self.refund_expired_bounty(bounty_id, creator_wallet)
                                if result["status"] == "success":
                                    logger.info(f"Auto-refunded bounty {bounty_id}")

                except Exception as e:
                    logger.error(f"Error in expiration watcher: {e}")

                await asyncio.sleep(interval)

        self._watcher_task = asyncio.create_task(watcher_loop())

    async def stop_expiration_watcher(self):
        """Stop the background expiration watcher."""
        if not self._watcher_running:
            return

        self._watcher_running = False
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
            self._watcher_task = None
        logger.info("🛑 Bounty expiration watcher stopped")

    async def get_bounty(self, bounty_id: str) -> Optional[Dict]:
        """Get a single bounty by ID."""
        path = self._get_path(bounty_id)
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    async def get_stats(self) -> Dict:
        """Get bounty board statistics."""
        stats = {
            "open": 0,
            "claimed": 0,
            "submitted": 0,
            "paid": 0,
            "expired": 0,
            "refunded": 0,
            "total_reward_posted": 0.0,
            "total_reward_paid": 0.0,
        }

        for fpath in self.bounty_dir.glob("*.json"):
            try:
                with open(fpath) as f:
                    data = json.load(f)
                    status = data.get("status", "UNKNOWN").lower()
                    if status in stats:
                        stats[status] += 1
                    stats["total_reward_posted"] += data.get("reward", 0)
                    if status == "paid":
                        stats["total_reward_paid"] += data.get("reward", 0)
            except Exception:
                continue

        return stats

if __name__ == "__main__":
    # Test Routine
    async def main():
        bb = BountyBoard()
        # 1. Post
        bid = await bb.post_bounty("Fix SSL", "Update Nginx certs", 50.0)
        
        # 2. Claim (Simulated Claimant)
        # Use a random devnet address for testing
        test_worker = "7sK7G5J2wPZD8hW6xY9qR3vU1o0aL4mN2eB5cV6jF8k" 
        await bb.claim_bounty(bid, test_worker)
        
        # 3. Submit
        await bb.submit_solution(bid, "github.com/mumega/pr/1")
        
        # 4. Pay (Will fail if no funds/auth, but logic runs)
        # res = await bb.approve_and_pay(bid)
        # print(res)
        
    asyncio.run(main())
