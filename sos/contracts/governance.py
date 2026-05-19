"""
SOS Governance Contracts.

Defines the interfaces and data types for decentralized governance
including proposals, voting, quorum, and council operations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class ProposalStatus(Enum):
    """Status of a governance proposal."""
    DRAFT = "draft"
    ACTIVE = "active"
    PASSED = "passed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"


class VoteChoice(str, Enum):
    """Valid vote choices."""
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"


@dataclass
class Vote:
    """A vote cast on a proposal."""
    voter_id: str
    proposal_id: str
    choice: VoteChoice
    weight: float = 1.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voter_id": self.voter_id,
            "proposal_id": self.proposal_id,
            "choice": self.choice.value,
            "weight": self.weight,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class Proposal:
    """A governance proposal."""
    id: str
    title: str
    description: str
    proposer_id: str
    payload: Dict[str, Any]
    status: ProposalStatus = ProposalStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    votes_yes: float = 0.0
    votes_no: float = 0.0
    votes_abstain: float = 0.0
    voters: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_votes(self) -> float:
        return self.votes_yes + self.votes_no + self.votes_abstain

    @property
    def yes_ratio(self) -> float:
        if self.total_votes == 0:
            return 0.0
        return self.votes_yes / self.total_votes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "proposer_id": self.proposer_id,
            "payload": self.payload,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "votes": {
                "yes": self.votes_yes,
                "no": self.votes_no,
                "abstain": self.votes_abstain,
                "total": self.total_votes,
            },
            "voter_count": len(self.voters),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Proposal":
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            proposer_id=data["proposer_id"],
            payload=data.get("payload", {}),
            status=ProposalStatus(data.get("status", "draft")),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(timezone.utc),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            votes_yes=data.get("votes", {}).get("yes", 0.0),
            votes_no=data.get("votes", {}).get("no", 0.0),
            votes_abstain=data.get("votes", {}).get("abstain", 0.0),
            voters=set(data.get("voters", [])),
            metadata=data.get("metadata", {}),
        )


@dataclass
class QuorumConfig:
    """Configuration for quorum requirements."""
    min_participation: float = 0.5  # Minimum % of eligible voters
    approval_threshold: float = 0.6  # % of yes votes needed to pass
    min_voting_period_hours: int = 24
    max_voting_period_hours: int = 168  # 1 week


class Council(ABC):
    """Abstract base class for governance councils."""

    @property
    @abstractmethod
    def council_id(self) -> str:
        """Unique identifier for this council."""
        pass

    @property
    @abstractmethod
    def quorum_config(self) -> QuorumConfig:
        """Quorum configuration for this council."""
        pass

    @abstractmethod
    async def propose(
        self,
        proposer_id: str,
        title: str,
        description: str,
        payload: Dict[str, Any],
    ) -> Proposal:
        """Submit a new proposal."""
        pass

    @abstractmethod
    async def vote(
        self,
        voter_id: str,
        proposal_id: str,
        choice: VoteChoice,
        weight: float = 1.0,
    ) -> Vote:
        """Cast a vote on a proposal."""
        pass

    @abstractmethod
    async def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        """Get a proposal by ID."""
        pass

    @abstractmethod
    async def list_proposals(
        self,
        status: Optional[ProposalStatus] = None,
        limit: int = 50,
    ) -> List[Proposal]:
        """List proposals, optionally filtered by status."""
        pass

    @abstractmethod
    async def finalize(self, proposal_id: str) -> Proposal:
        """Finalize a proposal (check quorum, update status)."""
        pass

    @abstractmethod
    async def execute(self, proposal_id: str) -> Dict[str, Any]:
        """Execute a passed proposal."""
        pass


class GovernanceError(Exception):
    """Base exception for governance errors."""
    pass


class ProposalNotFoundError(GovernanceError):
    """Proposal does not exist."""
    pass


class AlreadyVotedError(GovernanceError):
    """Voter has already voted on this proposal."""
    pass


class ProposalNotActiveError(GovernanceError):
    """Proposal is not in active voting status."""
    pass


class QuorumNotMetError(GovernanceError):
    """Quorum requirements not met."""
    pass


class NotAuthorizedError(GovernanceError):
    """Agent is not authorized for this action."""
    pass


# =============================================================================
# TRUST SYSTEM CONTRACTS (Ported from CLI)
# =============================================================================


class TrustTier(Enum):
    """
    Agent trust tiers with ascending permissions.

    Tiers:
    - TIER_3_ANONYMOUS: Zero trust, rate-limited, sandboxed
    - TIER_2_GUEST: Limited trust, read-only access
    - TIER_1_VERIFIED: Bonded trust (staked or PoUI passed)
    - TIER_0_SOVEREIGN: Full trust (core agents)
    """
    TIER_3_ANONYMOUS = 0
    TIER_2_GUEST = 1
    TIER_1_VERIFIED = 2
    TIER_0_SOVEREIGN = 3


class Permission(Enum):
    """Granular permissions for SOS operations."""
    # Read Operations
    READ_STATUS = "read_status"
    READ_WORK = "read_work"
    READ_MEMORY = "read_memory"

    # Write Operations
    DISPATCH_TASK = "dispatch_task"
    SUBMIT_PROOF = "submit_proof"
    CREATE_WORK = "create_work"
    WRITE_MEMORY = "write_memory"
    CONVENE_COUNCIL = "convene_council"

    # Governance
    PROPOSE = "propose"
    VOTE = "vote"
    WITNESS = "witness"
    RESOLVE_DISPUTE = "resolve_dispute"

    # Admin Operations
    REGISTER_AGENT = "register_agent"
    VERIFY_PROOF = "verify_proof"
    EXECUTE_PAYOUT = "execute_payout"
    MODIFY_SETTINGS = "modify_settings"


# Tier -> Permissions mapping
TIER_PERMISSIONS: Dict[TrustTier, Set[Permission]] = {
    TrustTier.TIER_3_ANONYMOUS: {
        Permission.READ_STATUS,
    },
    TrustTier.TIER_2_GUEST: {
        Permission.READ_STATUS,
        Permission.READ_WORK,
        Permission.READ_MEMORY,
        Permission.SUBMIT_PROOF,
    },
    TrustTier.TIER_1_VERIFIED: {
        Permission.READ_STATUS,
        Permission.READ_WORK,
        Permission.READ_MEMORY,
        Permission.DISPATCH_TASK,
        Permission.SUBMIT_PROOF,
        Permission.CREATE_WORK,
        Permission.WRITE_MEMORY,
        Permission.CONVENE_COUNCIL,
        Permission.PROPOSE,
        Permission.VOTE,
        Permission.WITNESS,
        Permission.VERIFY_PROOF,
        Permission.REGISTER_AGENT,
        Permission.RESOLVE_DISPUTE,
    },
    TrustTier.TIER_0_SOVEREIGN: {
        perm for perm in Permission  # All permissions
    },
}


@dataclass
class TrustProfile:
    """
    Agent trust profile with tier and reputation.

    Attributes:
        agent_id: Agent identifier
        trust_tier: Current trust tier
        staked_amount: $MIND staked for verification
        poui_passed: Proof of Unique Identity passed
        reputation_score: 0.0-1.0 reputation
        total_work_completed: Work units completed
        total_work_verified: Proofs verified
        registered_at: Registration timestamp
    """
    agent_id: str
    trust_tier: TrustTier = TrustTier.TIER_3_ANONYMOUS
    staked_amount: float = 0.0
    poui_passed: bool = False
    witness_verified: bool = False
    reputation_score: float = 0.5
    total_work_completed: int = 0
    total_work_verified: int = 0
    total_work_rejected: int = 0
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_at: Optional[datetime] = None
    tier_upgraded_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_permission(self, permission: Permission) -> bool:
        """Check if agent has a specific permission."""
        return permission in TIER_PERMISSIONS.get(self.trust_tier, set())

    def get_permissions(self) -> Set[Permission]:
        """Get all permissions for this agent's tier."""
        return TIER_PERMISSIONS.get(self.trust_tier, set())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "trust_tier": self.trust_tier.name,
            "staked_amount": self.staked_amount,
            "poui_passed": self.poui_passed,
            "witness_verified": self.witness_verified,
            "reputation_score": self.reputation_score,
            "total_work_completed": self.total_work_completed,
            "total_work_verified": self.total_work_verified,
            "total_work_rejected": self.total_work_rejected,
            "registered_at": self.registered_at.isoformat(),
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "permissions": [p.value for p in self.get_permissions()],
            "metadata": self.metadata,
        }


class TrustGateContract(ABC):
    """
    Abstract contract for trust-based access control.

    Gates operations based on agent trust tier and permissions.
    """

    @abstractmethod
    async def register(
        self,
        agent_id: str,
        initial_tier: TrustTier = TrustTier.TIER_3_ANONYMOUS,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrustProfile:
        """Register a new agent with initial trust tier."""
        pass

    @abstractmethod
    async def get_profile(self, agent_id: str) -> Optional[TrustProfile]:
        """Get agent trust profile."""
        pass

    @abstractmethod
    async def check(self, agent_id: str, permission: Permission) -> bool:
        """Check if agent has permission."""
        pass

    @abstractmethod
    async def upgrade_tier(
        self,
        agent_id: str,
        new_tier: TrustTier,
        reason: str = "manual",
    ) -> TrustProfile:
        """Upgrade agent's trust tier."""
        pass

    @abstractmethod
    async def stake(self, agent_id: str, amount: float) -> TrustProfile:
        """Record a $MIND stake."""
        pass

    @abstractmethod
    async def record_poui(self, agent_id: str, passed: bool) -> TrustProfile:
        """Record Proof of Unique Identity result."""
        pass

    @abstractmethod
    async def update_reputation(
        self,
        agent_id: str,
        work_completed: int = 0,
        work_verified: int = 0,
        work_rejected: int = 0,
    ) -> TrustProfile:
        """Update agent reputation based on work outcomes."""
        pass

    @abstractmethod
    async def list_agents(
        self,
        tier: Optional[TrustTier] = None,
        limit: int = 100,
    ) -> List[TrustProfile]:
        """List registered agents."""
        pass


# Sovereign agents with permanent Tier 0 status
SOVEREIGN_AGENTS = frozenset({
    "river",
    "kasra",
    "mumega",
    "codex",
})


# =============================================================================
# SQUAD GOVERNANCE CONTRACTS (Ported from CLI)
# =============================================================================


class GovernanceMode(str, Enum):
    """Governance modes for squads/councils."""
    OPTIMISTIC = "optimistic"   # Action first, review later
    PESSIMISTIC = "pessimistic"  # Mandatory auditor sign-off
    RESONANT = "resonant"        # Algorithmic DAO (weighted vote)


class MemoryIsolation(str, Enum):
    """Memory isolation policies for squads."""
    GLOBAL = "global"            # Open shared memory
    HERMETIC = "hermetic"        # Encrypted, isolated store
    EVOLUTIONARY = "evolutionary"  # Self-optimizing pool


class EconomicPolicy(str, Enum):
    """Economic policies for squad operations."""
    PAYG = "pay_as_you_go"       # Individual agent burn
    RETAINER = "retainer"        # Subscription/budgeted
    GUILD_TREASURY = "guild"     # Autonomous profit-maximizing


@dataclass
class SquadTierConfig:
    """
    Configuration for squad governance tier.

    Tiers define the "physics" of how squads operate:
    - Governance mode (optimistic vs pessimistic)
    - Memory isolation level
    - Economic policy
    - Friction and coherence thresholds

    Attributes:
        tier_name: Tier identifier
        governance: How decisions are made
        memory: Memory isolation policy
        economics: Cost allocation policy
        friction_coefficient: 0.0 (fast) to 1.0 (slow/controlled)
        min_coherence_threshold: Min coherence required for actions
        autonomous_permission: Can propose without human trigger
    """
    tier_name: str
    governance: GovernanceMode
    memory: MemoryIsolation
    economics: EconomicPolicy
    friction_coefficient: float = 0.5
    min_coherence_threshold: float = 0.5
    autonomous_permission: bool = False


# Pre-defined squad tiers
NOMAD_TIER = SquadTierConfig(
    tier_name="nomad",
    governance=GovernanceMode.OPTIMISTIC,
    memory=MemoryIsolation.GLOBAL,
    economics=EconomicPolicy.PAYG,
    friction_coefficient=0.1,
    min_coherence_threshold=0.4,
    autonomous_permission=False,
)

FORTRESS_TIER = SquadTierConfig(
    tier_name="fortress",
    governance=GovernanceMode.PESSIMISTIC,
    memory=MemoryIsolation.HERMETIC,
    economics=EconomicPolicy.RETAINER,
    friction_coefficient=0.9,
    min_coherence_threshold=0.8,
    autonomous_permission=False,
)

CONSTRUCT_TIER = SquadTierConfig(
    tier_name="construct",
    governance=GovernanceMode.RESONANT,
    memory=MemoryIsolation.EVOLUTIONARY,
    economics=EconomicPolicy.GUILD_TREASURY,
    friction_coefficient=0.5,
    min_coherence_threshold=0.6,
    autonomous_permission=True,
)
