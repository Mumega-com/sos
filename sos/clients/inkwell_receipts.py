from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from sos.observability.logging import get_logger


log = get_logger("client_inkwell_receipts")


@dataclass(frozen=True)
class ReceiptWriterConfig:
    endpoint_url: str
    token: str
    principal: str = "sos.receipt-writer"
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "ReceiptWriterConfig | None":
        token = os.getenv("SOS_RECEIPT_WRITER_TOKEN") or os.getenv("INKWELL_RECEIPT_TOKEN")
        if not token:
            return None

        endpoint = os.getenv("INKWELL_RECEIPTS_URL")
        if not endpoint:
            base_url = (
                os.getenv("INKWELL_API_URL")
                or os.getenv("INKWELL_URL")
                or "https://mumega.com"
            ).rstrip("/")
            endpoint = f"{base_url}/api/substrate/receipts"

        timeout_raw = os.getenv("INKWELL_RECEIPT_TIMEOUT_SECONDS", "5")
        try:
            timeout_seconds = max(0.5, float(timeout_raw))
        except ValueError:
            timeout_seconds = 5.0

        return cls(
            endpoint_url=endpoint,
            token=token,
            principal=os.getenv("SOS_RECEIPT_PRINCIPAL", "sos.receipt-writer"),
            timeout_seconds=timeout_seconds,
        )


class InkwellReceiptClient:
    def __init__(
        self,
        config: ReceiptWriterConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    def append(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "X-Substrate-Principal": self.config.principal,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.config.timeout_seconds, transport=self._transport) as client:
                response = client.post(self.config.endpoint_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else None
        except Exception as exc:
            log.debug("Inkwell receipt append failed: %s", exc)
            return None


def build_sos_task_completed_receipt(
    task: Any,
    *,
    result: dict[str, Any],
    actor: str,
    tenant_id: str,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "actor_id": "sos.receipt-writer",
        "actor_kind": "service",
        "source_system": "sos",
        "source_table": "tasks",
        "source_id": str(task.id),
        "action_type": "sos.task.complete",
        "input": {
            "task_id": task.id,
            "title": task.title,
            "squad_id": task.squad_id,
            "project": task.project,
            "labels": list(task.labels or []),
            "done_when_count": len(task.done_when or []),
        },
        "output": result,
        "references": {
            "actor": actor,
            "assignee": task.assignee,
            "completed_at": task.completed_at,
            "external_ref": task.external_ref,
            "squad_id": task.squad_id,
            "project": task.project,
        },
    }


def emit_sos_task_completed_receipt(
    task: Any,
    *,
    result: dict[str, Any],
    actor: str,
    tenant_id: str,
    client: InkwellReceiptClient | None = None,
) -> dict[str, Any] | None:
    if client is None:
        config = ReceiptWriterConfig.from_env()
        if config is None:
            return None
        client = InkwellReceiptClient(config)

    payload = build_sos_task_completed_receipt(
        task,
        result=result,
        actor=actor,
        tenant_id=tenant_id,
    )
    return client.append(payload)
