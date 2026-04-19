"""ToRivers-SOS Bridge -- expose SOS workflows as marketplace automations.

When a ToRivers user runs an automation, the bridge:
1. Receives execution request from ToRivers
2. Creates an SOS Objective (v0.8.0 living objective tree primitive)
3. Polls the objective until it reaches terminal state (paid) or times out
4. Returns the completion artifact to ToRivers
5. ToRivers charges the user's wallet

This makes every SOS service available on the marketplace.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from sos.clients.objectives import AsyncObjectivesClient

logger = logging.getLogger("sos.adapters.torivers")


# -- Polling tunables (module-level so tests can monkeypatch) -------------

_POLL_INTERVAL_S: float = 5.0
_POLL_TIMEOUT_S: float = 300.0


def _usd_to_mind(usd: float) -> int:
    """Convert a USD price into $MIND bounty units.

    v0.8.1 policy: flat 1 USD = 100 $MIND. See plan 2026-04-19 S1.3.
    """
    return int(round(float(usd) * 100))


# -- Available SOS workflows that can be sold on ToRivers ---------------

AVAILABLE_WORKFLOWS: list[dict[str, Any]] = [
    {
        "name": "monthly-seo-audit",
        "description": "Full SEO audit: analytics + search console + recommendations",
        "category": "seo",
        "price": 25.00,
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "tenant": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "report": {"type": "string"},
                "recommendations": {"type": "array"},
            },
        },
        "sos_service": "analytics",
        "sos_action": "ingest_and_decide",
    },
    {
        "name": "content-calendar",
        "description": "Generate 8 SEO blog posts based on keyword research",
        "category": "content",
        "price": 40.00,
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "posts": {"type": "array"},
            },
        },
        "sos_service": "content",
        "sos_action": "generate_calendar",
    },
    {
        "name": "wordpress-page-build",
        "description": "Build an Elementor page from blueprint using SitePilotAI",
        "category": "web",
        "price": 50.00,
        "input_schema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "blueprint": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "integer"},
                "url": {"type": "string"},
            },
        },
        "sos_service": "sitepilotai",
        "sos_action": "build_page",
    },
    {
        "name": "lead-outreach-campaign",
        "description": "Scrape targets, write emails, prepare outreach batch",
        "category": "outreach",
        "price": 35.00,
        "input_schema": {
            "type": "object",
            "properties": {
                "industry": {"type": "string"},
                "region": {"type": "string"},
                "count": {"type": "integer"},
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "leads": {"type": "array"},
                "emails_drafted": {"type": "integer"},
            },
        },
        "sos_service": "outreach",
        "sos_action": "run_campaign",
    },
    {
        "name": "sred-evidence-scan",
        "description": "Scan GitHub + QuickBooks for SR&ED eligible evidence",
        "category": "finance",
        "price": 500.00,
        "input_schema": {
            "type": "object",
            "properties": {
                "github_org": {"type": "string"},
                "quickbooks_id": {"type": "string"},
                "tax_year": {"type": "integer"},
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "evidence_items": {"type": "array"},
                "estimated_credit": {"type": "number"},
            },
        },
        "sos_service": "gaf",
        "sos_action": "scan_evidence",
    },
]


@dataclass
class ToRiversBridge:
    """Bridge between SOS Objectives Service and the ToRivers marketplace.

    Translates SOS workflows into ToRivers automations so they can be
    listed, purchased, and executed by marketplace users. Since v0.8.1,
    execution posts an Objective (not a Squad task) and polls it to paid
    state, returning the completion artifact to ToRivers.
    """

    # squad_url / bus_url / bus_token are retained for backwards compat with
    # the CLI; they are no longer used on the execute() path.
    squad_url: str
    bus_url: str
    bus_token: str
    torivers_api_url: str | None = None
    objectives_url: str | None = None
    _registered: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _objectives_client(self) -> AsyncObjectivesClient:
        base_url = (
            self.objectives_url
            or os.environ.get("SOS_OBJECTIVES_URL")
            or "http://localhost:6068"
        )
        return AsyncObjectivesClient(base_url=base_url)

    # -- Registration ---------------------------------------------------

    async def register_workflow(self, workflow: dict[str, Any]) -> str:
        """Register an SOS workflow as a ToRivers automation.

        Args:
            workflow: Workflow definition dict with name, description,
                      category, price, input/output schemas, and SOS
                      service/action mapping.

        Returns:
            automation_id: Unique ID for this automation on ToRivers.
        """
        name = workflow["name"]
        automation_id = f"tr-{name}-{uuid.uuid4().hex[:8]}"

        # TODO: POST to ToRivers API to create the automation listing
        # when ToRivers is deployed:
        #   async with httpx.AsyncClient(base_url=self.torivers_api_url) as client:
        #       resp = await client.post("/api/v1/automations", json={
        #           "id": automation_id,
        #           "name": name,
        #           "description": workflow.get("description", ""),
        #           "category": workflow.get("category", "general"),
        #           "price_cents": int(workflow.get("price", 0) * 100),
        #           "input_schema": workflow.get("input_schema", {}),
        #           "output_schema": workflow.get("output_schema", {}),
        #           "provider": "sos",
        #       })
        #       resp.raise_for_status()

        self._registered[automation_id] = workflow
        price = workflow.get("price", 0)
        logger.info(
            "Would register on ToRivers: %s at $%.2f/run (id=%s)",
            name,
            price,
            automation_id,
        )
        return automation_id

    # -- Execution ------------------------------------------------------

    async def execute(
        self,
        automation_id: str,
        input_data: dict[str, Any],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a ToRivers automation by creating and awaiting an SOS objective.

        Posts an Objective into the v0.8.0 living objective tree, then polls
        every ``_POLL_INTERVAL_S`` seconds up to ``_POLL_TIMEOUT_S`` for a
        terminal state. On ``state == "paid"`` the bridge returns the
        completion artifact. If the timeout elapses first the bridge returns
        a timeout envelope so the caller can refund the user's wallet.

        Args:
            automation_id: The automation ID returned from register_workflow.
            input_data: User-provided input matching the workflow's input_schema.
            user_id: Optional ToRivers user ID for billing attribution.

        Returns:
            Execution result dict with status, task_id, and (on completion)
            artifact URL.
        """
        workflow = self._registered.get(automation_id)
        if not workflow:
            return {"status": "error", "error": f"Unknown automation: {automation_id}"}

        name = workflow["name"]
        sos_service = workflow.get("sos_service", "general")
        sos_action = workflow.get("sos_action", "execute")
        price_usd = float(workflow.get("price", 0) or 0)
        bounty_mind = _usd_to_mind(price_usd)

        title = f"[ToRivers] {name}"
        description = (
            f"Marketplace execution for user {user_id or 'anonymous'}. "
            f"Service: {sos_service}, Action: {sos_action}. "
            f"Input keys: {sorted(input_data.keys())}"
        )
        tags = ["torivers", f"workflow:{name}"]
        capabilities_required = [sos_service] if sos_service else []

        client = self._objectives_client()

        # -- Create ------------------------------------------------------
        try:
            objective = await client.create(
                title=title,
                description=description,
                bounty_mind=bounty_mind,
                tags=tags,
                capabilities_required=capabilities_required,
                project="trop",
                created_by=f"torivers-bridge:{user_id or 'anonymous'}",
            )
        except Exception as exc:
            logger.error(
                "torivers bridge: objective create failed",
                extra={
                    "automation_id": automation_id,
                    "workflow": name,
                    "error": str(exc),
                },
            )
            return {"status": "error", "error": str(exc)}

        objective_id = objective.id
        logger.info(
            "torivers bridge: objective created",
            extra={
                "automation_id": automation_id,
                "workflow": name,
                "objective_id": objective_id,
                "bounty_mind": bounty_mind,
            },
        )

        # -- Poll until paid or timeout ---------------------------------
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _POLL_TIMEOUT_S
        while True:
            try:
                current = await client.get(objective_id)
            except Exception as exc:
                logger.warning(
                    "torivers bridge: poll get failed; retrying",
                    extra={
                        "objective_id": objective_id,
                        "workflow": name,
                        "error": str(exc),
                    },
                )
                current = None

            if current is not None and current.state == "paid":
                logger.info(
                    "torivers bridge: objective paid",
                    extra={
                        "automation_id": automation_id,
                        "workflow": name,
                        "objective_id": objective_id,
                    },
                )
                return {
                    "status": "completed",
                    "task_id": objective_id,
                    "automation_id": automation_id,
                    "workflow": name,
                    "artifact": current.completion_artifact_url,
                    "notes": current.completion_notes,
                }

            if loop.time() >= deadline:
                logger.info(
                    "torivers bridge: objective poll timed out",
                    extra={
                        "automation_id": automation_id,
                        "workflow": name,
                        "objective_id": objective_id,
                    },
                )
                return {
                    "status": "timeout",
                    "task_id": objective_id,
                    "automation_id": automation_id,
                    "workflow": name,
                }

            await asyncio.sleep(_POLL_INTERVAL_S)

    # -- Listing --------------------------------------------------------

    @staticmethod
    def list_available_workflows() -> list[dict[str, Any]]:
        """Return pre-defined SOS workflows available for ToRivers.

        These are the SOS capabilities that can be sold as marketplace
        automations. Each maps to an SOS service and action.
        """
        return AVAILABLE_WORKFLOWS

    async def register_all(self) -> list[str]:
        """Register all available workflows on ToRivers.

        Returns:
            List of automation IDs for each registered workflow.
        """
        ids: list[str] = []
        for wf in AVAILABLE_WORKFLOWS:
            automation_id = await self.register_workflow(wf)
            ids.append(automation_id)
        return ids
