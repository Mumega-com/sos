"""
Agent Delegation Service - Allows agents to delegate work to other agents.

Port from CLI's DelegationTool with recursion guards.

Usage:
    from sos.services.engine.delegation import DelegationService

    delegation = DelegationService(engine)
    result = await delegation.delegate("kasra", "Implement the login flow")
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from sos.observability.logging import get_logger
from sos.agents.definitions import ALL_AGENTS, AgentSoul

log = get_logger("delegation")

# Maximum delegation depth to prevent infinite recursion
MAX_DELEGATION_DEPTH = 3

# Agent lookup by name
AGENTS_BY_NAME: Dict[str, AgentSoul] = {
    agent.name.lower(): agent for agent in ALL_AGENTS
}


@dataclass
class DelegationContext:
    """Tracks the delegation chain to prevent loops."""
    chain: List[str] = field(default_factory=list)
    depth: int = 0
    original_agent: str = ""
    original_task: str = ""

    def can_delegate(self, target_agent: str) -> bool:
        """Check if delegation is allowed."""
        # Prevent recursion loops
        if target_agent.lower() in [a.lower() for a in self.chain]:
            return False
        # Prevent too deep chains
        if self.depth >= MAX_DELEGATION_DEPTH:
            return False
        return True

    def push(self, agent: str) -> "DelegationContext":
        """Create new context with agent added to chain."""
        return DelegationContext(
            chain=self.chain + [agent],
            depth=self.depth + 1,
            original_agent=self.original_agent or agent,
            original_task=self.original_task,
        )


class DelegationService:
    """
    Manages agent-to-agent delegation.

    When an agent delegates to another:
    1. The target agent's system prompt is loaded
    2. A new chat request is made with agent_override
    3. Results are returned to the delegating agent
    4. Recursion is guarded via delegation chain tracking
    """

    def __init__(self, engine):
        """
        Initialize with reference to SOSEngine.

        Args:
            engine: SOSEngine instance for making delegated calls
        """
        self.engine = engine
        self._active_delegations: Dict[str, DelegationContext] = {}

    def get_agent(self, name: str) -> Optional[AgentSoul]:
        """Get agent definition by name."""
        return AGENTS_BY_NAME.get(name.lower())

    def list_agents(self) -> List[str]:
        """List available agents for delegation."""
        return list(AGENTS_BY_NAME.keys())

    async def delegate(
        self,
        target_agent: str,
        task: str,
        source_agent: str = "river",
        conversation_id: Optional[str] = None,
        context: Optional[DelegationContext] = None,
    ) -> Dict[str, Any]:
        """
        Delegate a task to another agent.

        Args:
            target_agent: Name of agent to delegate to (e.g., "kasra")
            task: The task description
            source_agent: Agent making the delegation
            conversation_id: Optional conversation context
            context: Optional delegation context (for nested delegations)

        Returns:
            Dict with response, agent_used, and delegation_chain
        """
        target = target_agent.lower()

        # Validate target agent exists
        agent_soul = self.get_agent(target)
        if not agent_soul:
            return {
                "error": f"Unknown agent: {target_agent}",
                "available_agents": self.list_agents(),
                "success": False,
            }

        # Initialize or validate delegation context
        ctx = context or DelegationContext(
            original_agent=source_agent,
            original_task=task,
        )

        # Check recursion guard
        if not ctx.can_delegate(target):
            log.warn(
                f"Delegation blocked: {source_agent} -> {target}",
                chain=ctx.chain,
                depth=ctx.depth,
            )
            return {
                "error": f"Delegation blocked (max depth {MAX_DELEGATION_DEPTH} or loop detected)",
                "chain": ctx.chain,
                "success": False,
            }

        # Push to chain
        new_ctx = ctx.push(target)

        log.info(
            f"Delegating to {target}",
            source=source_agent,
            depth=new_ctx.depth,
            chain=new_ctx.chain,
        )

        # Build the delegated request
        # Import here to avoid circular dependency
        from sos.contracts.engine import ChatRequest

        delegated_request = ChatRequest(
            message=task,
            agent_id=f"agent:{target}",
            conversation_id=conversation_id or f"delegation-{source_agent}-{target}",
            model=agent_soul.model if agent_soul.model != "multi" else None,
            metadata={
                "is_delegated": True,
                "delegation_chain": new_ctx.chain,
                "delegation_depth": new_ctx.depth,
                "original_agent": new_ctx.original_agent,
                "skip_tools": True,  # Prevent tool loops
            },
        )

        try:
            # Execute via engine (will use agent's system prompt)
            response = await self.engine.chat(delegated_request)

            return {
                "response": response.content,
                "agent_used": target,
                "model_used": response.model_used,
                "delegation_chain": new_ctx.chain,
                "delegation_depth": new_ctx.depth,
                "success": True,
            }

        except Exception as e:
            log.error(f"Delegation failed: {e}", target=target, source=source_agent)
            return {
                "error": str(e),
                "agent_used": target,
                "delegation_chain": new_ctx.chain,
                "success": False,
            }

    async def delegate_with_consensus(
        self,
        agents: List[str],
        task: str,
        source_agent: str = "river",
    ) -> Dict[str, Any]:
        """
        Delegate to multiple agents and aggregate responses.

        Useful for getting multiple perspectives on a decision.

        Args:
            agents: List of agent names to consult
            task: The task/question
            source_agent: Agent making the delegation

        Returns:
            Dict with all responses and metadata
        """
        import asyncio

        results = {}
        tasks = []

        for agent in agents:
            tasks.append(self.delegate(agent, task, source_agent))

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for agent, resp in zip(agents, responses):
            if isinstance(resp, Exception):
                results[agent] = {"error": str(resp), "success": False}
            else:
                results[agent] = resp

        return {
            "responses": results,
            "agents_consulted": agents,
            "task": task,
        }
