"""
SOS Economy Service Contract.

The Economy Service handles:
- Work ledger and transaction history
- Payouts to agents for completed work
- Slashing for violations
- Wallet adapters (Solana, etc.)
- Treasury policies
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from enum import Enum

from sos.kernel import Capability


class TransactionType(Enum):
    """Types of economic transactions."""
    PAYOUT = "payout"           # Payment for completed work
    SLASH = "slash"             # Penalty for violations
    TRANSFER = "transfer"       # Agent-to-agent transfer
    DEPOSIT = "deposit"         # External deposit
    WITHDRAWAL = "withdrawal"   # External withdrawal
    REWARD = "reward"           # Bonus/incentive reward
    FEE = "fee"                 # Service fee
    USAGE_CHARGE = "usage_charge"   # Debit for a metered model-call (UsageEvent driven)
    SKILL_PAYOUT = "skill_payout"   # 85% creator share from a usage charge
    PLATFORM_FEE = "platform_fee"   # 15% platform share credited to agent:treasury


class TransactionStatus(Enum):
    """Transaction lifecycle status."""
    PROPOSED = "proposed"
    VALIDATED = "validated"
    PENDING_WITNESS = "pending_witness"
    WITNESSED = "witnessed"
    COMMITTED = "committed"
    SETTLED = "settled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class Transaction:
    """
    Economic transaction in SOS.

    Attributes:
        id: Unique transaction identifier
        tx_type: Type of transaction
        from_agent: Source agent (or "treasury" for system)
        to_agent: Destination agent
        amount: Transaction amount
        currency: Currency code (MIND, SOL, etc.)
        status: Current transaction status
        reason: Human-readable reason
        task_id: Related task ID (if applicable)
        witness_id: Witness who approved (if required)
        created_at: When transaction was created
        settled_at: When transaction was finalized
        metadata: Additional metadata
    """
    tx_type: TransactionType
    from_agent: str
    to_agent: str
    amount: int  # Integer units (e.g., cents, lamports)
    currency: str = "MIND"
    id: Optional[str] = None
    status: TransactionStatus = TransactionStatus.PROPOSED
    reason: str = ""
    task_id: Optional[str] = None
    witness_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    settled_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Balance:
    """Agent balance information."""
    agent_id: str
    currency: str
    available: int
    pending: int
    total_earned: int
    total_spent: int


@dataclass
class PayoutRequest:
    """Request for work payout."""
    agent_id: str
    amount: int
    currency: str
    task_id: str
    reason: str
    requires_witness: bool = False
    capability: Optional[Capability] = None


@dataclass
class SlashRequest:
    """Request for slashing penalty."""
    agent_id: str
    amount: int
    currency: str
    reason: str
    evidence: dict[str, Any]
    requires_witness: bool = True
    capability: Optional[Capability] = None


class EconomyContract(ABC):
    """
    Abstract contract for the SOS Economy Service.

    All Economy implementations must conform to this interface.
    """

    @abstractmethod
    async def get_balance(
        self,
        agent_id: str,
        currency: str = "MIND",
        capability: Optional[Capability] = None,
    ) -> Balance:
        """
        Get agent's balance.

        Args:
            agent_id: Agent to query
            currency: Currency code
            capability: Authorization capability

        Returns:
            Balance information
        """
        pass

    @abstractmethod
    async def payout(self, request: PayoutRequest) -> Transaction:
        """
        Issue payout for completed work.

        Args:
            request: Payout request

        Returns:
            Created transaction
        """
        pass

    @abstractmethod
    async def slash(self, request: SlashRequest) -> Transaction:
        """
        Apply slashing penalty.

        Args:
            request: Slash request

        Returns:
            Created transaction
        """
        pass

    @abstractmethod
    async def transfer(
        self,
        from_agent: str,
        to_agent: str,
        amount: int,
        currency: str = "MIND",
        reason: str = "",
        capability: Optional[Capability] = None,
    ) -> Transaction:
        """
        Transfer between agents.

        Args:
            from_agent: Source agent
            to_agent: Destination agent
            amount: Amount to transfer
            currency: Currency code
            reason: Transfer reason
            capability: Authorization capability

        Returns:
            Created transaction
        """
        pass

    @abstractmethod
    async def witness_approve(
        self,
        transaction_id: str,
        witness_id: str,
        capability: Optional[Capability] = None,
    ) -> Transaction:
        """
        Witness approves a pending transaction.

        Args:
            transaction_id: Transaction to approve
            witness_id: Witness approving
            capability: Authorization capability

        Returns:
            Updated transaction
        """
        pass

    @abstractmethod
    async def witness_reject(
        self,
        transaction_id: str,
        witness_id: str,
        reason: str,
        capability: Optional[Capability] = None,
    ) -> Transaction:
        """
        Witness rejects a pending transaction.

        Args:
            transaction_id: Transaction to reject
            witness_id: Witness rejecting
            reason: Rejection reason
            capability: Authorization capability

        Returns:
            Updated transaction
        """
        pass

    @abstractmethod
    async def get_transaction(
        self,
        transaction_id: str,
        capability: Optional[Capability] = None,
    ) -> Optional[Transaction]:
        """
        Get transaction by ID.

        Args:
            transaction_id: Transaction identifier
            capability: Authorization capability

        Returns:
            Transaction if found
        """
        pass

    @abstractmethod
    async def list_transactions(
        self,
        agent_id: Optional[str] = None,
        tx_type: Optional[TransactionType] = None,
        status: Optional[TransactionStatus] = None,
        limit: int = 100,
        offset: int = 0,
        capability: Optional[Capability] = None,
    ) -> list[Transaction]:
        """
        List transactions with filters.

        Args:
            agent_id: Filter by agent
            tx_type: Filter by type
            status: Filter by status
            limit: Max results
            offset: Pagination offset
            capability: Authorization capability

        Returns:
            List of matching transactions
        """
        pass

    @abstractmethod
    async def get_daily_totals(
        self,
        agent_id: str,
        currency: str = "MIND",
        capability: Optional[Capability] = None,
    ) -> dict[str, int]:
        """
        Get daily transaction totals for rate limiting.

        Args:
            agent_id: Agent to query
            currency: Currency code
            capability: Authorization capability

        Returns:
            Dict of tx_type -> total amount today
        """
        pass

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """
        Get economy service health status.

        Returns:
            Health status with stats
        """
        pass


# Wallet adapter interface
class WalletAdapter(ABC):
    """Abstract interface for blockchain wallet adapters."""

    @abstractmethod
    async def get_balance(self, address: str) -> int:
        """Get wallet balance."""
        pass

    @abstractmethod
    async def send(
        self,
        from_address: str,
        to_address: str,
        amount: int,
    ) -> str:
        """Send transaction, return tx hash."""
        pass

    @abstractmethod
    async def get_transaction(self, tx_hash: str) -> dict[str, Any]:
        """Get transaction details."""
        pass

    @property
    @abstractmethod
    def chain(self) -> str:
        """Chain name (solana, ethereum, etc.)."""
        pass


# =============================================================================
# WORK LEDGER CONTRACTS (Ported from CLI)
# =============================================================================


class WorkUnitStatus(Enum):
    """Work unit lifecycle status."""
    QUEUED = "queued"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    REJECTED = "rejected"
    DISPUTED = "disputed"
    PAID = "paid"


class ProofStatus(Enum):
    """Proof verification status."""
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class DisputeStatus(Enum):
    """Work dispute status."""
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED_WORKER_WINS = "resolved_worker_wins"
    RESOLVED_CHALLENGER_WINS = "resolved_challenger_wins"
    ESCALATED = "escalated"


@dataclass
class WorkUnit:
    """
    A unit of work in the SOS economy.

    Work flows: QUEUED → CLAIMED → IN_PROGRESS → SUBMITTED → VERIFIED → PAID
    (can branch to DISPUTED from VERIFIED)

    Attributes:
        id: Unique work identifier
        title: Brief description
        description: Full work specification
        requester_id: Agent requesting the work
        worker_id: Agent assigned to do the work
        status: Current work status
        payout_amount: Payment for completion
        payout_currency: Currency (MIND, etc.)
        dispute_window_seconds: Time to dispute after verification
        escrow_id: Escrow transaction ID (if funds locked)
        proof_id: Associated proof ID
        observer_id: Witness agent ID
        created_at: Creation timestamp
        completed_at: Completion timestamp
        metadata: Additional data
    """
    id: str
    title: str
    description: str
    requester_id: str
    status: WorkUnitStatus = WorkUnitStatus.QUEUED
    worker_id: Optional[str] = None
    payout_amount: Optional[int] = None
    payout_currency: str = "MIND"
    dispute_window_seconds: int = 3600
    escrow_id: Optional[str] = None
    proof_id: Optional[str] = None
    observer_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Proof:
    """
    Proof of work completion.

    Attributes:
        id: Unique proof identifier
        work_id: Associated work unit
        worker_id: Agent submitting proof
        output_ref: Reference to output (file, URL, etc.)
        output_hash: Hash of output for verification
        status: Verification status
        verification: Verification result data
        submitted_at: Submission timestamp
        metadata: Additional data
    """
    id: str
    work_id: str
    worker_id: str
    status: ProofStatus = ProofStatus.PENDING
    output_ref: Optional[str] = None
    output_hash: Optional[str] = None
    observer_id: Optional[str] = None
    verification: dict[str, Any] = field(default_factory=dict)
    submitted_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DisputeRecord:
    """
    Record of a work dispute for arbitration.

    Attributes:
        id: Unique dispute identifier
        work_id: Disputed work unit
        challenger_id: Agent raising dispute
        reason: Dispute reason
        status: Current dispute status
        resolver_id: Agent who resolved
        resolution_notes: Resolution explanation
        slash_amount: Penalty amount (if any)
        slash_target: Who gets slashed
        evidence_refs: References to evidence
        created_at: When dispute was raised
        resolved_at: When resolved
    """
    id: str
    work_id: str
    challenger_id: str
    reason: str
    status: DisputeStatus = DisputeStatus.OPEN
    resolver_id: Optional[str] = None
    resolution_notes: Optional[str] = None
    slash_amount: int = 0
    slash_target: Optional[str] = None
    evidence_refs: list[str] = field(default_factory=list)
    assigned_to: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreateWorkRequest:
    """Request to create a new work unit."""
    title: str
    description: str
    requester_id: str
    payout_amount: Optional[int] = None
    payout_currency: str = "MIND"
    dispute_window_seconds: int = 3600
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmitProofRequest:
    """Request to submit proof of work."""
    work_id: str
    worker_id: str
    output_ref: Optional[str] = None
    output_hash: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmitDisputeRequest:
    """Request to dispute verified work."""
    work_id: str
    challenger_id: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)


class WorkLedgerContract(ABC):
    """
    Abstract contract for the SOS Work Ledger.

    Manages work units, proofs, and disputes.
    """

    @abstractmethod
    async def create_work(self, request: CreateWorkRequest) -> WorkUnit:
        """Create a new work unit."""
        pass

    @abstractmethod
    async def get_work(self, work_id: str) -> Optional[WorkUnit]:
        """Get work unit by ID."""
        pass

    @abstractmethod
    async def list_work(
        self,
        status: Optional[WorkUnitStatus] = None,
        worker_id: Optional[str] = None,
        requester_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[WorkUnit]:
        """List work units with filters."""
        pass

    @abstractmethod
    async def claim_work(self, work_id: str, worker_id: str) -> WorkUnit:
        """Claim a queued work unit."""
        pass

    @abstractmethod
    async def start_work(self, work_id: str, worker_id: str) -> WorkUnit:
        """Start working on a claimed unit."""
        pass

    @abstractmethod
    async def submit_proof(self, request: SubmitProofRequest) -> Proof:
        """Submit proof of work completion."""
        pass

    @abstractmethod
    async def verify_proof(
        self,
        proof_id: str,
        verifier_id: str,
        approved: bool,
        verification: Optional[dict[str, Any]] = None,
    ) -> Proof:
        """Verify or reject a proof."""
        pass

    @abstractmethod
    async def submit_dispute(self, request: SubmitDisputeRequest) -> DisputeRecord:
        """Submit a dispute against verified work."""
        pass

    @abstractmethod
    async def resolve_dispute(
        self,
        dispute_id: str,
        resolver_id: str,
        worker_wins: bool,
        notes: str,
        slash_amount: int = 0,
    ) -> DisputeRecord:
        """Resolve a dispute."""
        pass

    @abstractmethod
    async def get_dispute(self, dispute_id: str) -> Optional[DisputeRecord]:
        """Get dispute by ID."""
        pass

    @abstractmethod
    async def list_disputes(
        self,
        status: Optional[DisputeStatus] = None,
        work_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[DisputeRecord]:
        """List disputes with filters."""
        pass
