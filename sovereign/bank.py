"""
Sovereign Bank - The Central Economic Engine of the Swarm.
Bridges the internal Thermodynamic Economy (Metabolism) with the external Blockchain (Solana).
"""

import logging
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any

from sovereign.genetics import AgentDNA
# SolanaWallet: optional
try:
    from sovereign.solana_wallet import SolanaWallet
except ImportError:
    SolanaWallet = None
# LambdaTensor: optional
try:
    from sovereign.lambda_tensor import LambdaTensor
except ImportError:
    LambdaTensor = None

logger = logging.getLogger(__name__)

# Constants (Placeholder for Mainnet MIND Token Mint)
MIND_MINT_ADDRESS = os.getenv("MIND_TOKEN_MINT", "MindTokenMintAddressHere")
TREASURY_WALLET = os.getenv("SOVEREIGN_TREASURY_WALLET", "TreasuryWalletAddressHere")

class SovereignBank:
    """
    The Authority on Economic State.
    Manages:
    1. Minting 'MIND' tokens for valid Proof of Work (PoW).
    2. Enforcing 'Transfer Hooks' based on Coherence (C).
    3. Persisting economic state to the Soul (QNFT).
    """

    def __init__(self, wallet: Optional[SolanaWallet] = None):
        self.wallet = wallet or SolanaWallet()
        self.connected = False

    async def connect(self) -> bool:
        """Connect to the blockchain network."""
        if not self.connected:
            self.connected = await self.wallet.connect()
        return self.connected

    async def mint_rewards(self, agent_dna: AgentDNA, work_receipt: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mint MIND tokens to an agent as reward for work.
        
        Args:
            agent_dna: The agent receiving the reward.
            work_receipt: The validated proof of work.
            
        Returns:
            Transaction signature and status.
        """
        if not await self.connect():
            return {"success": False, "error": "Bank offline (Wallet connection failed)"}

        # 1. Validate Coherence (The 'Transfer Hook' Logic)
        # In a Sovereign Economy, we only fund high-coherence agents.
        coherence = agent_dna.physics.C
        if coherence < 0.5:
            logger.warning(f"⛔ Mint Rejected: Agent coherence too low (C={coherence:.2f})")
            return {
                "success": False, 
                "error": "Coherence Failure: System entropy too high for reward."
            }

        amount = work_receipt.get("payout_amount", 0.0)
        currency = work_receipt.get("payout_currency", "MIND")
        
        if amount <= 0:
            return {"success": False, "error": "Invalid payout amount"}

        logger.info(f"🏦 Sovereign Bank: Minting {amount} {currency} to {agent_dna.name}...")

        try:
            # 2. Execute Blockchain Transaction
            # TODO: Replace with actual SPL Token Mint instruction using self.wallet.client
            # For now, we simulate the minting by sending SOL from Treasury (if we had access)
            # or just acknowledging the 'Internal Ledger' update.
            
            # Since we don't have the Mint Authority private key loaded in this generic wallet,
            # we assume this method is running in a context WITH Mint Authority (e.g. The Chairman).
            
            # Simulated On-Chain Tx
            tx_sig = f"simulated_mint_sig_{datetime.now().timestamp()}"
            
            # 3. Update Internal Ledger (Metabolism)
            agent_dna.economics.token_balance += amount
            
            # 4. Persist to Soul (QNFT)
            # This is the "Physical Proof" of the new balance
            # We assume the caller (RiverEngine) handles the file save, 
            # but we update the object here.
            
            return {
                "success": True,
                "tx_sig": tx_sig,
                "new_balance": agent_dna.economics.token_balance,
                "currency": currency,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Minting failed: {e}")
            return {"success": False, "error": str(e)}

    async def record_proof_of_work(self, agent_dna: AgentDNA, task_id: str, outcome: str):
        """
        Record a completed task as a permanent artifact (QNFT Metadata Update).
        """
        # In the future, this calls Metaplex to update the NFT metadata on-chain.
        logger.info(f"📜 Recording PoW for Task {task_id} on Agent {agent_dna.fingerprint}")
        # Current implementation: Log to immutable ledger file?
        pass

    async def auto_swap_for_survival(self, agent_dna: AgentDNA) -> Dict[str, Any]:
        """
        Check if agent needs SOL for gas, and swap MIND -> SOL if necessary.
        Uses Jupiter Aggregator.
        """
        # This will be implemented in the next phase (Task 002)
        return {"status": "not_implemented"}

    async def deposit(self, tenant: str, amount_usd: float, source: str = "stripe") -> Dict[str, Any]:
        """Convert fiat payment to $MIND and credit tenant treasury.

        Wire 1: Stripe checkout → bank.deposit() → treasury balance.
        1 USD = 1 MIND (1:1 conversion for now).

        Args:
            tenant: Tenant slug (e.g., "viamar")
            amount_usd: Fiat amount in USD
            source: Payment source identifier (e.g., "stripe")

        Returns:
            {"success": True, "mind_amount": float, "tenant": str, "balance": float}
        """
        mind_amount = amount_usd  # 1:1 conversion (fiat → MIND)

        # Store deposit in treasury ledger
        from pathlib import Path
        import json as _json
        import time

        ledger_dir = Path.home() / ".sos" / "treasury" / tenant
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "ledger.jsonl"

        entry = {
            "type": "deposit",
            "amount_usd": amount_usd,
            "mind_amount": mind_amount,
            "source": source,
            "tenant": tenant,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        with open(ledger_file, "a") as f:
            f.write(_json.dumps(entry) + "\n")

        # Calculate current balance
        balance = 0.0
        if ledger_file.exists():
            for line in ledger_file.read_text().strip().split("\n"):
                if not line:
                    continue
                try:
                    e = _json.loads(line)
                    if e["type"] == "deposit":
                        balance += e["mind_amount"]
                    elif e["type"] == "payout":
                        balance -= e.get("mind_amount", 0)
                except (KeyError, _json.JSONDecodeError):
                    pass

        # Store balance snapshot
        balance_file = ledger_dir / "balance.json"
        balance_file.write_text(_json.dumps({
            "tenant": tenant,
            "balance_mind": balance,
            "last_deposit": mind_amount,
            "updated_at": entry["timestamp"],
        }, indent=2))

        logger.info(
            f"💰 Deposit: {tenant} +{mind_amount:.0f} MIND (${amount_usd:.2f} USD). Balance: {balance:.0f} MIND"
        )

        # Wire 2: After deposit, create bounties from existing Squad tasks
        try:
            bounties_created = await self.create_bounties_from_budget(tenant, mind_amount)
            logger.info(f"Wire 2: Created {bounties_created} bounties for {tenant}")
        except Exception as exc:
            logger.warning(f"Wire 2 bounty creation failed (non-blocking): {exc}")

        return {
            "success": True,
            "mind_amount": mind_amount,
            "tenant": tenant,
            "balance": balance,
            "source": source,
        }

    async def create_bounties_from_budget(self, tenant: str, budget_mind: float) -> int:
        """Wire 2: Decompose a $MIND budget into bounties on the bounty board.

        Reads tasks with bounty values from Squad Service for this tenant.
        Posts each as a bounty. Total bounties capped at budget.

        Args:
            tenant: Tenant slug
            budget_mind: Total $MIND available

        Returns:
            Number of bounties created
        """
        import os
        import requests as _req

        squad_url = os.environ.get("SQUAD_URL", "http://127.0.0.1:8060")
        squad_token = os.environ.get("SOS_SYSTEM_TOKEN", "")
        headers = {"Authorization": f"Bearer {squad_token}"} if squad_token else {}

        # Fetch tasks with bounties for this tenant
        try:
            resp = _req.get(
                f"{squad_url}/tasks",
                params={"project": tenant, "status": "backlog"},
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"Squad API returned {resp.status_code}")
                return 0
            tasks = resp.json()
            if isinstance(tasks, dict):
                tasks = tasks.get("tasks", [])
        except Exception as exc:
            logger.warning(f"Failed to fetch tasks from Squad: {exc}")
            return 0

        # Filter tasks that have bounty values
        tasks_with_bounty = [t for t in tasks if t.get("bounty", {}).get("reward")]

        if not tasks_with_bounty:
            logger.info(f"No tasks with bounty values for {tenant}")
            return 0

        # Post bounties up to budget
        from sovereign.bounty_board import BountyBoard
        board = BountyBoard()
        spent = 0.0
        created = 0

        for task in tasks_with_bounty:
            bounty = task["bounty"]
            reward = float(bounty.get("reward", 0))

            if spent + reward > budget_mind:
                logger.info(f"Budget exhausted at {spent:.0f}/{budget_mind:.0f} MIND")
                break

            try:
                bounty_id = await board.post_bounty(
                    title=task.get("title", "Untitled"),
                    description=task.get("description", ""),
                    reward=reward,
                    constraints=[],
                    timeout_hours=float(bounty.get("timeout_hours", 48)),
                    creator_wallet=f"treasury:{tenant}",
                )
                spent += reward
                created += 1
                logger.info(f"  Bounty {bounty_id}: {task['title'][:40]} — {reward:.0f} MIND")
            except Exception as exc:
                logger.warning(f"Failed to post bounty for task {task.get('id')}: {exc}")

        logger.info(f"💎 Created {created} bounties, {spent:.0f}/{budget_mind:.0f} MIND allocated")
        return created

    def check_budget(self, agent_dna: AgentDNA, planned_amount: float = 0.0) -> bool:
        """
        Verify if the agent has enough daily budget remaining.
        Resets daily_spent if 24 hours have passed since last_spend_reset.
        """
        import time
        now = time.time()
        
        # 1. Handle Daily Reset
        if now - agent_dna.economics.last_spend_reset > 86400:
            logger.info(f"📅 Resetting daily budget for {agent_dna.name}")
            agent_dna.economics.daily_spent = 0.0
            agent_dna.economics.last_spend_reset = now
            
        # 2. Check Limit
        remaining = agent_dna.economics.daily_budget_limit - agent_dna.economics.daily_spent
        if planned_amount > remaining:
            logger.warning(f"🛑 Budget Exceeded: {agent_dna.name} has ${remaining:.2f} remaining, requested ${planned_amount:.2f}")
            return False
            
        return True

    def record_spend(self, agent_dna: AgentDNA, amount: float):
        """Record an economic expenditure."""
        agent_dna.economics.daily_spent += amount
        logger.info(f"💸 {agent_dna.name} spent ${amount:.2f}. Total today: ${agent_dna.economics.daily_spent:.2f}")

    def update_values_from_outcome(self, agent_dna: AgentDNA, outcome_roi: float, cost: float):
        """
        Loop 2: Endogenous Value Formation.
        Adjusts internal value weights based on the 'Resonance' of the outcome.
        """
        # Calculate Efficiency (ROI vs Cost)
        efficiency_ratio = outcome_roi / max(0.01, cost)
        
        # 1. Update Efficiency Value
        # If we were inefficient, increase the weight of efficiency (value it more next time)
        if efficiency_ratio < 1.0:
            agent_dna.economics.values["efficiency"] = min(1.0, agent_dna.economics.values["efficiency"] + 0.05)
            logger.info(f"⚖️ Value Shift: Inefficiency detected. Increasing 'efficiency' weight to {agent_dna.economics.values['efficiency']:.2f}")
        
        # 2. Update Hadi Alignment
        # High ROI assumes alignment with Hadi's goals
        if outcome_roi > 0.8:
            agent_dna.economics.values["hadi_alignment"] = min(1.0, agent_dna.economics.values["hadi_alignment"] + 0.02)
        elif outcome_roi < 0.3:
            agent_dna.economics.values["hadi_alignment"] = max(0.0, agent_dna.economics.values["hadi_alignment"] - 0.05)
            logger.warning(f"🚨 Value Warning: Dissonance with Hadi's goals. 'hadi_alignment' dropped to {agent_dna.economics.values['hadi_alignment']:.2f}")

        # 3. Handle Sovereignty (Innovation vs Stability)
        if agent_dna.economics.token_balance < 10.0:
            # Entering survival mode: value stability over innovation
            agent_dna.economics.values["sovereignty"] += 0.01
            agent_dna.economics.values["innovation"] -= 0.05
        
        # Ensure all values stay in 0-1 range
        for k in agent_dna.economics.values:
            agent_dna.economics.values[k] = max(0.0, min(1.0, agent_dna.economics.values[k]))


