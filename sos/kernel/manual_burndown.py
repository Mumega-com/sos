from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ManualRoutingCandidate:
    id: str
    source_event: str
    destination: str
    automation: str
    status: str
    owner: str
    failure_mode: str
    verification: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


TOP_FIVE: tuple[ManualRoutingCandidate, ...] = (
    ManualRoutingCandidate(
        id="startup_sprint_context",
        source_event="model/session first load",
        destination="boot_context + sprint_capsule",
        automation="boot_context embeds current S061 sprint capsule",
        status="live-partial",
        owner="loom",
        failure_mode="agent reads local files and wastes context",
        verification="MCP boot_context includes sprint.current_slice",
    ),
    ManualRoutingCandidate(
        id="notion_decision_to_task_state",
        source_event="Notion Judgment Board decision",
        destination="Squad task state + project event stream",
        automation="S060 inbound decision application plus S061 event router",
        status="live-partial",
        owner="codex",
        failure_mode="decision stays in Notion and Hadi copies it manually",
        verification="S060 inbound and S061 event-router regression tests",
    ),
    ManualRoutingCandidate(
        id="task_update_to_human_surface",
        source_event="Squad task create/update/complete/fail",
        destination="Discord/Notion/canonical project event stream",
        automation="S061 InformationEvent router with public-safe projection allowlist",
        status="live-partial",
        owner="codex",
        failure_mode="task changes on one surface but humans/agents miss it",
        verification="/health/flow event_router gate",
    ),
    ManualRoutingCandidate(
        id="tenant_node_context_to_startup",
        source_event="join-with-invite/login",
        destination="boot_context + onboarding graph",
        automation="Node Join Contract and read-only onboarding graph",
        status="live-partial",
        owner="codex",
        failure_mode="new computer becomes a separate truth island",
        verification="/health/flow node_join gate",
    ),
    ManualRoutingCandidate(
        id="routing_outcome_to_dreamer",
        source_event="Brain duplicate/handoff/dead-agent routing outcome",
        destination="Dreamer routing outcome input",
        automation="Brain routing outcome stream mirrored to project Dreamer stream",
        status="live-partial",
        owner="codex",
        failure_mode="bad routes repeat until Hadi notices manually",
        verification="Brain outcome and ProjectDreamer routing-outcome tests",
    ),
)


def top_manual_routing_candidates() -> list[dict[str, str]]:
    return [candidate.to_dict() for candidate in TOP_FIVE]
