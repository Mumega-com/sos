"""GHL bridge — Sprint 008 S008-C / G78.

Knight protocol library for GoHighLevel CRM integration.
Pulls contacts/deals/calendar via MCP tools, maps to GTM graph.

V1: single GHL account (mcp__ghl-mumega__*), all knights share.
Periodic pull every 15 min per knight. Event-triggered push on demand.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger("sos.gtm.ghl_bridge")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GhlBridgeError(RuntimeError):
    """GHL API call failed."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class GhlEntityMappingError(RuntimeError):
    """GHL response didn't match expected schema."""


# ---------------------------------------------------------------------------
# GHL pull operations (read)
# ---------------------------------------------------------------------------


def pull_contacts_for_owner(
    ghl_call: Any,
    ghl_user_id: str | None = None,
    location_id: str | None = None,
) -> list[dict[str, Any]]:
    """Pull contacts from GHL, optionally filtered by owner.

    Args:
        ghl_call: callable that invokes GHL MCP tool (e.g., mcp__ghl-mumega__search_contacts)
        ghl_user_id: GHL user ID to filter by assigned_to
        location_id: GHL location ID

    Returns list of contact dicts with keys: id, name, email, phone, source.
    """
    try:
        params: dict[str, Any] = {"limit": 100}
        if location_id:
            params["locationId"] = location_id

        raw = ghl_call("search_contacts", **params)
        contacts = raw.get("contacts", []) if isinstance(raw, dict) else []

        result = []
        for c in contacts:
            if not isinstance(c, dict):
                continue
            # Filter by owner if specified
            if ghl_user_id and c.get("assignedTo") != ghl_user_id:
                continue
            result.append({
                "ghl_id": c.get("id", ""),
                "name": f"{c.get('firstName', '')} {c.get('lastName', '')}".strip(),
                "email": c.get("email"),
                "phone": c.get("phone"),
                "source": "ghl",
            })
        return result

    except Exception as exc:
        if "429" in str(exc) or "rate" in str(exc).lower():
            raise GhlBridgeError("GHL rate limit hit", status=429, retry_after=60) from exc
        raise GhlBridgeError(f"pull_contacts failed: {exc}") from exc


def pull_deals_for_owner(
    ghl_call: Any,
    pipeline_id: str,
    ghl_user_id: str | None = None,
    location_id: str | None = None,
) -> list[dict[str, Any]]:
    """Pull deals/opportunities from GHL pipeline."""
    try:
        params: dict[str, Any] = {"pipelineId": pipeline_id}
        if location_id:
            params["location_id"] = location_id

        raw = ghl_call("search_opportunities", **params)
        opps = raw.get("opportunities", []) if isinstance(raw, dict) else []

        result = []
        for opp in opps:
            if not isinstance(opp, dict):
                continue
            if ghl_user_id and opp.get("assignedTo") != ghl_user_id:
                continue
            result.append({
                "ghl_opportunity_id": opp.get("id", ""),
                "name": opp.get("name", ""),
                "stage": opp.get("pipelineStageId", ""),
                "value_cents": round(float(opp.get("monetaryValue", 0)) * 100),
                "contact_id": opp.get("contactId"),
                "source": "ghl",
            })
        return result

    except GhlBridgeError:
        raise
    except Exception as exc:
        raise GhlBridgeError(f"pull_deals failed: {exc}") from exc


def pull_calendar_for_owner(
    ghl_call: Any,
    calendar_id: str,
    days_ahead: int = 14,
) -> list[dict[str, Any]]:
    """Pull upcoming calendar events."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)

    try:
        raw = ghl_call(
            "get_calendar_events",
            calendarId=calendar_id,
            startTime=now.isoformat(),
            endTime=end.isoformat(),
        )
        events = raw.get("events", []) if isinstance(raw, dict) else []

        result = []
        for evt in events:
            if not isinstance(evt, dict):
                continue
            result.append({
                "ghl_event_id": evt.get("id", ""),
                "title": evt.get("title", ""),
                "start_time": evt.get("startTime"),
                "end_time": evt.get("endTime"),
                "contact_id": evt.get("contactId"),
            })
        return result

    except GhlBridgeError:
        raise
    except Exception as exc:
        raise GhlBridgeError(f"pull_calendar failed: {exc}") from exc


# ---------------------------------------------------------------------------
# GHL push operations (write)
# ---------------------------------------------------------------------------


def update_deal_stage(
    ghl_call: Any,
    opportunity_id: str,
    new_stage_id: str,
    pipeline_id: str,
) -> bool:
    """Update a GHL opportunity's pipeline stage."""
    try:
        ghl_call(
            "update_opportunity",
            id=opportunity_id,
            pipelineId=pipeline_id,
            pipelineStageId=new_stage_id,
        )
        log.info("ghl_bridge: deal %s moved to stage %s", opportunity_id, new_stage_id)
        return True
    except Exception as exc:
        raise GhlBridgeError(f"update_deal_stage failed: {exc}") from exc


def schedule_followup(
    ghl_call: Any,
    contact_id: str,
    due_at: datetime,
    note: str,
) -> str:
    """Create a follow-up task in GHL for a contact."""
    try:
        result = ghl_call(
            "create_contact_task",
            contactId=contact_id,
            title=f"Follow up: {note[:50]}",
            body=note,
            dueDate=due_at.isoformat(),
        )
        task_id = result.get("task", {}).get("id", "") if isinstance(result, dict) else ""
        log.info("ghl_bridge: follow-up scheduled for contact %s, task_id=%s", contact_id, task_id)
        return task_id
    except Exception as exc:
        raise GhlBridgeError(f"schedule_followup failed: {exc}") from exc


def log_call_outcome(
    ghl_call: Any,
    contact_id: str,
    outcome: str,
    notes: str,
) -> bool:
    """Log a call outcome note on a GHL contact."""
    try:
        ghl_call(
            "create_contact_note",
            contactId=contact_id,
            body=f"Call outcome: {outcome}\n\n{notes}",
        )
        log.info("ghl_bridge: call outcome logged for contact %s: %s", contact_id, outcome)
        return True
    except Exception as exc:
        raise GhlBridgeError(f"log_call_outcome failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Sync: GHL → GTM graph
# ---------------------------------------------------------------------------


def sync_contacts_to_graph(
    conn: Any,
    ghl_call: Any,
    ghl_user_id: str | None = None,
    location_id: str | None = None,
) -> dict[str, int]:
    """Pull GHL contacts and upsert into gtm.people."""
    from sos.services.gtm.graph import upsert_person

    contacts = pull_contacts_for_owner(ghl_call, ghl_user_id, location_id)
    created = 0
    for c in contacts:
        try:
            upsert_person(conn, name=c["name"], email=c.get("email"), phone=c.get("phone"), source="ghl")
            created += 1
        except Exception as exc:
            log.warning("ghl_bridge: person upsert failed for %s: %s", c.get("name"), exc)

    return {"synced": created, "total": len(contacts)}


def sync_deals_to_graph(
    conn: Any,
    ghl_call: Any,
    pipeline_id: str,
    owner_knight_id: str | None = None,
    ghl_user_id: str | None = None,
    location_id: str | None = None,
    product: str = "gaf",
) -> dict[str, int]:
    """Pull GHL opportunities and upsert into gtm.deals."""
    from sos.services.gtm.graph import upsert_deal

    deals = pull_deals_for_owner(ghl_call, pipeline_id, ghl_user_id, location_id)
    created = 0
    for d in deals:
        try:
            upsert_deal(
                conn,
                person_id=None,  # resolve via contact_id mapping in S009
                company_id=None,
                product=product,
                stage=d["stage"],
                value_cents=d.get("value_cents"),
                owner_knight_id=owner_knight_id,
                ghl_opportunity_id=d.get("ghl_opportunity_id"),
            )
            created += 1
        except Exception as exc:
            log.warning("ghl_bridge: deal upsert failed for %s: %s", d.get("name"), exc)

    return {"synced": created, "total": len(deals)}
