from __future__ import annotations

import httpx

from sos.clients.inkwell_receipts import (
    InkwellReceiptClient,
    ReceiptWriterConfig,
    build_sos_task_completed_receipt,
    emit_sos_task_completed_receipt,
)
from sos.contracts.squad import SquadTask, TaskPriority, TaskStatus


def _task() -> SquadTask:
    return SquadTask(
        id="task-123",
        squad_id="squad-a",
        title="Ship receipt bridge",
        description="",
        status=TaskStatus.DONE,
        priority=TaskPriority.MEDIUM,
        assignee="agent:codex",
        project="mumega",
        labels=["integrity"],
        result={"summary": "done"},
        completed_at="2026-05-02T15:40:00Z",
        external_ref="loom:s037",
    )


def test_build_sos_task_completed_receipt_shape() -> None:
    payload = build_sos_task_completed_receipt(
        _task(),
        result={"summary": "done"},
        actor="agent:codex",
        tenant_id="tenant-a",
    )

    assert payload["tenant_id"] == "tenant-a"
    assert payload["actor_id"] == "sos.receipt-writer"
    assert payload["actor_kind"] == "service"
    assert payload["source_system"] == "sos"
    assert payload["source_table"] == "tasks"
    assert payload["source_id"] == "task-123"
    assert payload["action_type"] == "sos.task.complete"
    assert payload["input"]["squad_id"] == "squad-a"
    assert payload["output"] == {"summary": "done"}
    assert payload["references"]["external_ref"] == "loom:s037"


def test_client_posts_with_substrate_headers() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["principal"] = request.headers.get("X-Substrate-Principal")
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "ok": True,
                "duplicate": False,
                "receipt": {"id": "r1", "chain_seq": 2, "h_self": "abc"},
            },
        )

    client = InkwellReceiptClient(
        ReceiptWriterConfig(
            endpoint_url="https://mumega.com/api/substrate/receipts",
            token="test-token",
        ),
        transport=httpx.MockTransport(handler),
    )

    out = client.append({"source_system": "sos"})

    assert out and out["ok"] is True
    assert seen["url"] == "https://mumega.com/api/substrate/receipts"
    assert seen["auth"] == "Bearer test-token"
    assert seen["principal"] == "sos.receipt-writer"
    assert '"source_system":"sos"' in str(seen["body"])


def test_emit_disabled_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SOS_RECEIPT_WRITER_TOKEN", raising=False)
    monkeypatch.delenv("INKWELL_RECEIPT_TOKEN", raising=False)

    assert emit_sos_task_completed_receipt(
        _task(),
        result={"summary": "done"},
        actor="agent:codex",
        tenant_id="tenant-a",
    ) is None
