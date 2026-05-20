"""Public-safe agent profile contracts.

The public SOS core does not ship Mumega's named internal agents. Hosts can
register their own profiles through a plugin or service overlay.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentProfile:
    name: str
    title: str = ""
    tagline: str = ""
    model: str = "multi"
    roles: tuple[str, ...] = field(default_factory=tuple)


PUBLIC_AGENT_PROFILES: tuple[AgentProfile, ...] = ()

AGENT_PROFILES_BY_NAME: dict[str, AgentProfile] = {
    profile.name.lower(): profile for profile in PUBLIC_AGENT_PROFILES
}
