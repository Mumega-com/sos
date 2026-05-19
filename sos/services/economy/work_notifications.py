"""
Work Dispute Notifications
==========================
Best-effort notifications for dispute assignment and SLA breaches.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

#from mumega.config.settings import settings  # TODO: wire to SOS config
from .work_ledger import DisputeRecord, WorkUnit
from .worker_registry import WorkerProfile

logger = logging.getLogger(__name__)


class DisputeNotifier:
    """Send best-effort notifications for dispute events."""

    def __init__(self) -> None:
        self.webhook_url = os.getenv("MUMEGA_DISPUTE_WEBHOOK_URL", "").strip()
        self.telegram_token = settings.telegram.bot_token
        self.default_chat_id = (
            os.getenv("MUMEGA_DISPUTE_TELEGRAM_CHAT_ID", "").strip()
            or settings.daemon.admin_user_id
        )

    def notify_assignment(
        self,
        dispute: DisputeRecord,
        work_unit: Optional[WorkUnit] = None,
        arbiter: Optional[WorkerProfile] = None,
    ) -> None:
        payload = self._build_payload("dispute_assigned", dispute, work_unit, arbiter)
        self._send_webhook(payload)
        self._send_telegram(payload, arbiter=arbiter)

    def notify_overdue(
        self,
        dispute: DisputeRecord,
        work_unit: Optional[WorkUnit] = None,
        arbiter: Optional[WorkerProfile] = None,
    ) -> None:
        payload = self._build_payload("dispute_overdue", dispute, work_unit, arbiter)
        self._send_webhook(payload)
        self._send_telegram(payload, arbiter=arbiter)

    def _build_payload(
        self,
        event: str,
        dispute: DisputeRecord,
        work_unit: Optional[WorkUnit],
        arbiter: Optional[WorkerProfile],
    ) -> Dict[str, Any]:
        return {
            "event": event,
            "dispute": dispute.to_dict(),
            "work": work_unit.to_dict() if work_unit else None,
            "arbiter": arbiter.__dict__ if arbiter else None,
        }

    def _send_webhook(self, payload: Dict[str, Any]) -> None:
        if not self.webhook_url:
            return
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(self.webhook_url, json=payload)
        except Exception as exc:
            logger.warning("Dispute webhook notification failed: %s", exc)

    def _send_telegram(self, payload: Dict[str, Any], arbiter: Optional[WorkerProfile] = None) -> None:
        if not self.telegram_token:
            return
        chat_id = self._resolve_chat_id(arbiter)
        if not chat_id:
            return

        event = payload.get("event", "dispute_event")
        dispute = payload.get("dispute", {})
        work = payload.get("work", {})

        title = work.get("title") or "Unknown Work"
        status = dispute.get("status", "unknown")
        due_at = dispute.get("due_at") or "n/a"
        assigned_to = dispute.get("assigned_to") or "unassigned"

        message = (
            f"⚖️ {event.replace('_', ' ').title()}\n"
            f"Work: {title}\n"
            f"Dispute: {dispute.get('id')}\n"
            f"Status: {status}\n"
            f"Assigned: {assigned_to}\n"
            f"Due: {due_at}"
        )

        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(
                    f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                    json={"chat_id": chat_id, "text": message},
                )
        except Exception as exc:
            logger.warning("Telegram dispute notification failed: %s", exc)

    def _resolve_chat_id(self, arbiter: Optional[WorkerProfile]) -> Optional[str]:
        if arbiter:
            meta = arbiter.metadata or {}
            chat_id = meta.get("telegram_chat_id") or meta.get("telegram_chat")
            if chat_id:
                return str(chat_id)
        if self.default_chat_id:
            return str(self.default_chat_id)
        return None
