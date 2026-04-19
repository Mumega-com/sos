"""AgentPort — managed-agent provisioning, config, and per-tenant budget.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how the platform provisions per-tenant Anthropic /
Mumega agents and tracks their spend.

Tenant binding: EXPLICIT tenant_id on every method. An agent always
belongs to a tenant; the Mothership plane may reach across.

Agent identity: the richer soul-level identity lives in
sos.contracts.agent_card.AgentCard + sos.kernel.identity.Identity. This
port handles the CONFIG / BUDGET layer only — think "agent as a managed
product", not "agent as a being". For the broader view, join on
(tenant_id, agent_id).
"""
from __future__ import annotations

from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


AgentModel = Literal["haiku", "sonnet", "opus"]
AgentStatus = Literal["active", "paused", "provisioning", "error"]
UsageWindow = Literal["today", "month", "all"]


# --- Request / response models ---------------------------------------------


class McpServerRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    token: Optional[str] = None


class AgentConfig(BaseModel):
    """Managed-agent configuration — mirrors Inkwell's AgentConfig."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str = Field(
        description="Tenant-scoped agent identifier (slug)."
    )
    model: AgentModel
    system_prompt: str
    mcp_servers: list[McpServerRef] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    budget_per_day: int = Field(
        ge=0, description="Max daily spend in cents."
    )
    budget_per_month: int = Field(
        ge=0, description="Max monthly spend in cents."
    )
    status: AgentStatus = "provisioning"
    anthropic_agent_id: Optional[str] = Field(
        default=None, description="Provider-side id once provisioned."
    )
    created_at: str
    updated_at: str


class ProvisionRequest(BaseModel):
    """Create a new managed agent for a tenant. `status`/`created_at`/
    `updated_at` are assigned by the service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str
    model: AgentModel
    system_prompt: str
    mcp_servers: list[McpServerRef] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    budget_per_day: int = Field(ge=0)
    budget_per_month: int = Field(ge=0)
    anthropic_agent_id: Optional[str] = None


class GetConfigRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str


class AgentConfigPatch(BaseModel):
    """Partial update for an AgentConfig. All fields optional."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: Optional[AgentModel] = None
    system_prompt: Optional[str] = None
    mcp_servers: Optional[list[McpServerRef]] = None
    tools: Optional[list[str]] = None
    budget_per_day: Optional[int] = Field(default=None, ge=0)
    budget_per_month: Optional[int] = Field(default=None, ge=0)
    status: Optional[AgentStatus] = None


class UpdateConfigRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str
    patch: AgentConfigPatch


class RecordAgentUsageRequest(BaseModel):
    """Usage event for budget tracking (one call / one session slice)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str
    tokens: int = Field(ge=0, description="Combined input+output tokens.")
    cost: int = Field(ge=0, description="Cost in cents.")


class AgentUsage(BaseModel):
    """One usage row — mirrors Inkwell's AgentUsage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str
    date: str = Field(description="YYYY-MM-DD bucket.")
    session_hours: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cents: int = 0


class GetUsageRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str
    window: UsageWindow = "today"


class BudgetCheckRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    agent_id: str
    cost: int = Field(
        ge=0, description="Proposed cost in cents — will it fit the budget?"
    )


class BudgetCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    remaining_cents: int
    reason: Optional[str] = None


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class AgentPort(Protocol):
    """Managed-agent configuration and budget tracking."""

    async def provision(self, req: ProvisionRequest) -> AgentConfig:
        """Create a new managed agent for a tenant."""
        ...

    async def get_config(self, req: GetConfigRequest) -> Optional[AgentConfig]:
        """Current config for (tenant_id, agent_id) — None if not provisioned."""
        ...

    async def update_config(self, req: UpdateConfigRequest) -> AgentConfig:
        """Apply a partial patch, returns the updated config."""
        ...

    async def record_usage(self, req: RecordAgentUsageRequest) -> None:
        """Record one usage event for budget tracking."""
        ...

    async def get_usage(self, req: GetUsageRequest) -> list[AgentUsage]:
        """Usage rows for the requested window."""
        ...

    async def check_budget(self, req: BudgetCheckRequest) -> BudgetCheckResult:
        """Would a `cost`-cent charge fit today's and this month's budget?"""
        ...


__all__ = [
    "AgentModel",
    "AgentStatus",
    "UsageWindow",
    "McpServerRef",
    "AgentConfig",
    "AgentConfigPatch",
    "ProvisionRequest",
    "GetConfigRequest",
    "UpdateConfigRequest",
    "RecordAgentUsageRequest",
    "AgentUsage",
    "GetUsageRequest",
    "BudgetCheckRequest",
    "BudgetCheckResult",
    "AgentPort",
]
