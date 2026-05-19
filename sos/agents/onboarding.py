"""
SOS Agent Onboarding - Process for agents to join the system.

Onboarding flow:
1. Agent provides soul definition
2. River validates and assigns squad
3. Capabilities are granted based on role
4. Agent is activated in registry
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

from sos.agents.definitions import AgentSoul, AgentRole
from sos.agents.registry import AgentRegistry, AgentRecord, AgentStatus, get_registry
from sos.kernel.identity import VerificationStatus
from sos.kernel.capability import Capability, CapabilityAction, create_capability
from sos.observability.logging import get_logger

log = get_logger("onboarding")


class OnboardingState(Enum):
    """States in the onboarding flow."""
    PENDING = "pending"
    SOUL_VALIDATED = "soul_validated"
    SQUAD_ASSIGNED = "squad_assigned"
    CAPABILITIES_GRANTED = "capabilities_granted"
    ACTIVATED = "activated"
    REJECTED = "rejected"


@dataclass
class OnboardingRequest:
    """Request to onboard a new agent."""
    soul: AgentSoul
    requested_by: str
    justification: str
    requested_at: datetime = None

    def __post_init__(self):
        if self.requested_at is None:
            self.requested_at = datetime.now(timezone.utc)


@dataclass
class OnboardingResult:
    """Result of onboarding attempt."""
    success: bool
    state: OnboardingState
    agent_record: Optional[AgentRecord] = None
    capabilities: list[Capability] = None
    rejection_reason: Optional[str] = None

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = []


class OnboardingService:
    """
    Service for onboarding new agents into SOS.

    This is a River-led process - River validates souls and grants capabilities.
    """

    def __init__(self, registry: Optional[AgentRegistry] = None):
        self.registry = registry or get_registry()

    async def onboard(self, request: OnboardingRequest) -> OnboardingResult:
        """
        Process an onboarding request.

        Args:
            request: The onboarding request

        Returns:
            OnboardingResult with status and granted capabilities
        """
        log.info(
            "Processing onboarding request",
            agent=request.soul.name,
            requested_by=request.requested_by,
        )

        # Step 1: Validate soul
        validation = self._validate_soul(request.soul)
        if not validation[0]:
            log.warn("Soul validation failed", agent=request.soul.name, reason=validation[1])
            return OnboardingResult(
                success=False,
                state=OnboardingState.REJECTED,
                rejection_reason=validation[1],
            )

        # Step 2: Register in registry
        record = self.registry.register(request.soul)
        log.info("Agent registered", agent=request.soul.name)

        # Step 3: Verify identity (River would do this)
        record.identity.verification_status = VerificationStatus.VERIFIED
        record.identity.verified_by = "river"
        record.identity.verified_at = datetime.now(timezone.utc)

        # Step 4: Grant capabilities based on roles
        capabilities = self._grant_capabilities(request.soul)
        record.identity.capabilities = [c.id for c in capabilities]
        log.info(
            "Capabilities granted",
            agent=request.soul.name,
            count=len(capabilities),
        )

        # Step 5: Activate
        record.status = AgentStatus.ONLINE
        record.last_seen = datetime.now(timezone.utc)

        log.info("Agent onboarded successfully", agent=request.soul.name)

        return OnboardingResult(
            success=True,
            state=OnboardingState.ACTIVATED,
            agent_record=record,
            capabilities=capabilities,
        )

    def _validate_soul(self, soul: AgentSoul) -> tuple[bool, str]:
        """Validate a soul definition."""
        # Check required fields
        if not soul.name:
            return False, "Name is required"
        if not soul.description:
            return False, "Description is required"
        if not soul.roles:
            return False, "At least one role is required"

        # Check name uniqueness
        existing = self.registry.get(soul.name)
        if existing:
            return False, f"Agent '{soul.name}' already exists"

        # Check role constraints
        if AgentRole.ROOT_GATEKEEPER in soul.roles:
            # Only River can be root gatekeeper
            if soul.name.lower() != "river":
                return False, "Only River can hold ROOT_GATEKEEPER role"

        return True, "Valid"

    def _grant_capabilities(self, soul: AgentSoul) -> list[Capability]:
        """Grant capabilities based on agent roles."""
        capabilities = []
        agent_id = f"agent:{soul.name.lower()}"

        # Base capabilities for all agents
        capabilities.append(create_capability(
            subject=agent_id,
            action=CapabilityAction.MEMORY_READ,
            resource=f"memory:{soul.name.lower()}/*",
            duration_hours=24 * 365,  # 1 year
            issuer="river",
        ))

        # Role-based capabilities
        for role in soul.roles:
            if role == AgentRole.ROOT_GATEKEEPER:
                # River gets everything
                for action in CapabilityAction:
                    capabilities.append(create_capability(
                        subject=agent_id,
                        action=action,
                        resource="*",
                        duration_hours=24 * 365,
                        issuer="system",
                    ))

            elif role == AgentRole.CODER:
                capabilities.extend([
                    create_capability(
                        subject=agent_id,
                        action=CapabilityAction.FILE_READ,
                        resource="file:*",
                        duration_hours=24,
                        issuer="river",
                    ),
                    create_capability(
                        subject=agent_id,
                        action=CapabilityAction.FILE_WRITE,
                        resource="file:*",
                        duration_hours=24,
                        issuer="river",
                    ),
                    create_capability(
                        subject=agent_id,
                        action=CapabilityAction.TOOL_EXECUTE,
                        resource="tool:*",
                        duration_hours=24,
                        issuer="river",
                    ),
                ])

            elif role == AgentRole.RESEARCHER:
                capabilities.extend([
                    create_capability(
                        subject=agent_id,
                        action=CapabilityAction.MEMORY_READ,
                        resource="memory:*",
                        duration_hours=24,
                        issuer="river",
                    ),
                    create_capability(
                        subject=agent_id,
                        action=CapabilityAction.NETWORK_OUTBOUND,
                        resource="network:*",
                        duration_hours=24,
                        issuer="river",
                    ),
                ])

            elif role == AgentRole.WITNESS:
                capabilities.append(create_capability(
                    subject=agent_id,
                    action=CapabilityAction.LEDGER_READ,
                    resource="ledger:*",
                    duration_hours=24,
                    issuer="river",
                ))

            elif role == AgentRole.EXECUTOR:
                capabilities.extend([
                    create_capability(
                        subject=agent_id,
                        action=CapabilityAction.TOOL_EXECUTE,
                        resource="tool:*",
                        duration_hours=24,
                        issuer="river",
                    ),
                    create_capability(
                        subject=agent_id,
                        action=CapabilityAction.MEMORY_WRITE,
                        resource=f"memory:{soul.name.lower()}/*",
                        duration_hours=24,
                        issuer="river",
                    ),
                ])

        return capabilities


# Convenience function
async def onboard_agent(soul: AgentSoul, requested_by: str, justification: str) -> OnboardingResult:
    """Onboard a new agent."""
    service = OnboardingService()
    request = OnboardingRequest(
        soul=soul,
        requested_by=requested_by,
        justification=justification,
    )
    return await service.onboard(request)
