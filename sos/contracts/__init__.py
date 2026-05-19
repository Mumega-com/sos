"""
SOS Service Contracts - Abstract interfaces for all SOS services.

These contracts define the API surface for each service. Implementations
must conform to these interfaces. This enables:
- Service substitution (swap implementations)
- Testing with mocks
- Clear boundaries between services
"""

from sos.contracts.engine import EngineContract
from sos.contracts.memory import MemoryContract
from sos.contracts.economy import EconomyContract
from sos.contracts.tools import ToolsContract
from sos.contracts.governance import (
    Council,
    Proposal,
    ProposalStatus,
    Vote,
    VoteChoice,
    QuorumConfig,
    GovernanceError,
    ProposalNotFoundError,
    AlreadyVotedError,
    ProposalNotActiveError,
    QuorumNotMetError,
    NotAuthorizedError,
)

__all__ = [
    "EngineContract",
    "MemoryContract",
    "EconomyContract",
    "ToolsContract",
    # Governance
    "Council",
    "Proposal",
    "ProposalStatus",
    "Vote",
    "VoteChoice",
    "QuorumConfig",
    "GovernanceError",
    "ProposalNotFoundError",
    "AlreadyVotedError",
    "ProposalNotActiveError",
    "QuorumNotMetError",
    "NotAuthorizedError",
]
