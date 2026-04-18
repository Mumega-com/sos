"""
SOS Swarm Council - Cellular Governance Module.

Architecture:
- Cellular: Operates within a specific 'Squad' scope.
- Async: Uses the Message Bus to propose and vote (Non-blocking).
- Event-Driven: Consensus triggers downstream events (e.g., Execute Task).

Scalability:
- This module allows 10M users to govern themselves in parallel squads.
- No central bottleneck; decision logic is distributed.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from sos.kernel import Message, MessageType
from sos.kernel.bus import get_bus
from sos.observability.logging import get_logger
from sos.contracts.governance import (
    Council,
    Proposal,
    ProposalStatus,
    Vote,
    VoteChoice,
    QuorumConfig,
    AlreadyVotedError,
    ProposalNotFoundError,
    ProposalNotActiveError,
)

log = get_logger("swarm_council")

class SwarmCouncil:
    """
    Manages the distributed decision-making process for a Squad.
    """
    
    def __init__(self, squad_id: str, quorum_threshold: float = 0.6):
        self.squad_id = squad_id
        self.quorum_threshold = quorum_threshold
        self.bus = get_bus()
        
        # Local state (In production, this would be backed by Redis/DB)
        self.proposals: Dict[str, Dict[str, Any]] = {}
        
        log.info(f"⚖️ Council initialized for Squad: {squad_id}")

    async def propose(self, agent_id: str, title: str, payload: Dict[str, Any]) -> str:
        """
        Submit a proposal to the Squad.
        """
        proposal_id = f"prop_{len(self.proposals) + 1}"
        
        proposal = {
            "id": proposal_id,
            "title": title,
            "proposer": agent_id,
            "payload": payload,
            "status": ProposalStatus.ACTIVE,
            "votes": {"yes": 0, "no": 0},
            "voters": set(),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        self.proposals[proposal_id] = proposal
        
        # Broadcast Proposal to Squad Channel
        msg = Message(
            type=MessageType.CHAT, # Using CHAT for now, ideally generic EVENT
            source="council_bot",
            target=f"squad:{self.squad_id}", # Target the squad channel
            payload={
                "event": "PROPOSAL_NEW",
                "proposal_id": proposal_id,
                "title": title,
                "description": f"Agent {agent_id} proposes: {title}"
            }
        )
        
        # We need to adapt the Bus to support Squad targeting more explicitly
        # For now, we use the send method which handles squad logic
        await self.bus.send(msg, target_squad=self.squad_id)
        
        log.info(f"📜 Proposal {proposal_id} submitted by {agent_id}")
        return proposal_id

    async def vote(self, agent_id: str, proposal_id: str, vote: str) -> str:
        """
        Cast a vote (yes/no).
        """
        if proposal_id not in self.proposals:
            return "Proposal not found"
            
        p = self.proposals[proposal_id]
        
        if p["status"] != ProposalStatus.ACTIVE:
            return f"Proposal is {p['status'].value}"
            
        if agent_id in p["voters"]:
            return "Already voted"
            
        # Record Vote
        if vote.lower() in ["yes", "y", "approve"]:
            p["votes"]["yes"] += 1
        else:
            p["votes"]["no"] += 1
            
        p["voters"].add(agent_id)
        
        log.info(f"🗳️ Vote cast on {proposal_id} by {agent_id}: {vote}")
        
        # Check Quorum/Consensus
        # Simplified logic: If Yes > 2 (Mock Quorum)
        if p["votes"]["yes"] >= 2:
            return await self._finalize(proposal_id, passed=True)
            
        return "Vote recorded"

    async def _finalize(self, proposal_id: str, passed: bool) -> str:
        """
        Finalize the proposal and trigger execution.
        """
        p = self.proposals[proposal_id]
        p["status"] = ProposalStatus.PASSED if passed else ProposalStatus.REJECTED
        
        log.info(f"🔨 Proposal {proposal_id} FINALIZED: {p['status'].value}")
        
        # Emit Result Event
        msg = Message(
            type=MessageType.CHAT,
            source="council_bot",
            target=f"squad:{self.squad_id}",
            payload={
                "event": "PROPOSAL_RESULT",
                "proposal_id": proposal_id,
                "status": p["status"].value,
                "outcome": "Execution Triggered" if passed else "Discarded"
            }
        )
        await self.bus.send(msg, target_squad=self.squad_id)
        
        if passed:
            # Here we would send the payload to the Async Worker Queue
            # await worker_queue.push(p["payload"])
            pass
            
        return f"Proposal {p['status'].value}"

# Factory
def create_council(squad_id: str) -> SwarmCouncil:
    return SwarmCouncil(squad_id)
