from __future__ import annotations

import json
from typing import Any

from sos.services.etsy.order_closure import (
    HttpOrderClosureClient,
    OrderClosureConfig,
    OrderClosureService,
    close_order_from_asset_payload,
    closure_request_from_asset_payload,
)


class _Redis:
    def __init__(self) -> None:
        self.xadds: list[tuple[str, dict[str, str]]] = []
        self.publishes: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def xadd(self, stream: str, fields: dict[str, str], **kwargs: Any) -> str:
        self.xadds.append((stream, fields))
        return f"{len(self.xadds)}-0"

    def publish(self, channel: str, message: str) -> int:
        self.publishes.append((channel, message))
        return 1

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    def delete(self, *names: str) -> int:
        count = 0
        for name in names:
            if name in self.values:
                count += 1
                del self.values[name]
            self.deleted.append(name)
        return count


class _Closer:
    name = "test-closer"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def close_order(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return {"status": "closed", "closure_id": f"etsy-{request['receipt_id']}"}


class _Response:
    status_code = 200

    def json(self) -> dict[str, str]:
        return {"status": "closed", "closure_id": "closure-123"}


class _Session:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
        self.posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response()


def _asset_payload() -> dict[str, Any]:
    return {
        "source": "etsy-asset-forge",
        "shop_id": "shop-123",
        "receipt_id": "receipt-789",
        "field_label": "Name for artwork",
        "personalization_value": "Hadi <Owner>",
        "asset_key": "etsy/shop-123/receipt-789/v1.svg",
        "content_type": "image/svg+xml",
        "fulfillment_url": "https://r2.example/etsy/shop-123/receipt-789/v1.svg",
        "uploader": "http-r2-bridge",
        "pii_excluded": True,
    }


def test_closure_request_excludes_personalization_text() -> None:
    request = closure_request_from_asset_payload(_asset_payload(), project="sos")
    encoded = json.dumps(request)

    assert request["receipt_id"] == "receipt-789"
    assert request["shop_id"] == "shop-123"
    assert request["fulfillment_url"].startswith("https://r2.example/")
    assert request["pii_excluded"] is True
    assert "Hadi" not in encoded
    assert "Name for artwork" not in encoded


def test_close_order_calls_closer_and_builds_public_payload() -> None:
    closer = _Closer()

    result = close_order_from_asset_payload(_asset_payload(), closer=closer, project="sos")

    assert closer.requests[0]["receipt_id"] == "receipt-789"
    assert result["closure_status"] == "closed"
    assert result["closure_reference"] == "etsy-receipt-789"
    assert result["payload"]["closure_method"] == "test-closer"
    assert result["payload"]["pii_excluded"] is True


def test_order_closure_processes_asset_forged_and_emits_order_closed() -> None:
    redis = _Redis()
    service = OrderClosureService(
        redis_client=redis,
        closer=_Closer(),
        config=OrderClosureConfig(project="sos", tenant="sos", target_agent="hermes"),
    )
    fields = {
        "event_type": "asset.forged",
        "entity_id": "receipt-789",
        "payload": json.dumps(_asset_payload()),
    }

    result = service.process_event(stream="sos:stream:project:sos:events", entry_id="1-0", fields=fields)

    assert result["closed"] is True
    assert redis.xadds[0][0] == "sos:stream:project:sos:events"
    assert redis.xadds[0][1]["event_type"] == "order.closed"
    assert redis.xadds[1][0] == "sos:stream:project:sos:agent:hermes"
    assert redis.xadds[1][1]["type"] == "order.closed"
    assert redis.values["sos:etsy:order_closure:sos:receipt-789"] == "complete"


def test_order_closure_ignores_non_asset_events() -> None:
    redis = _Redis()
    closer = _Closer()
    service = OrderClosureService(
        redis_client=redis,
        closer=closer,
        config=OrderClosureConfig(project="sos", tenant="sos", target_agent="hermes"),
    )

    result = service.process_event(
        stream="sos:stream:project:sos:events",
        entry_id="1-0",
        fields={"event_type": "personalization.detected", "payload": "{}"},
    )

    assert result == {"ok": True, "skipped": True, "reason": "event_type"}
    assert closer.requests == []
    assert redis.xadds == []


def test_order_closure_dedupes_receipt_before_external_call() -> None:
    redis = _Redis()
    redis.set("sos:etsy:order_closure:sos:receipt-789", "complete")
    closer = _Closer()
    service = OrderClosureService(
        redis_client=redis,
        closer=closer,
        config=OrderClosureConfig(project="sos", tenant="sos", target_agent="hermes"),
    )

    result = service.process_event(
        stream="sos:stream:project:sos:events",
        entry_id="1-0",
        fields={"event_type": "asset.forged", "entity_id": "receipt-789", "payload": json.dumps(_asset_payload())},
    )

    assert result == {"ok": True, "skipped": True, "reason": "duplicate"}
    assert closer.requests == []
    assert redis.xadds == []


def test_http_order_closure_client_contract() -> None:
    session = _Session()
    client = HttpOrderClosureClient("https://closure.example", "token-123", session=session)

    result = client.close_order({"receipt_id": "receipt-789", "fulfillment_url": "https://r2.example/file.svg"})

    assert result == {"status": "closed", "closure_id": "closure-123"}
    assert session.posts[0]["url"] == "https://closure.example/close-order"
    assert session.posts[0]["headers"]["Authorization"] == "Bearer token-123"
    assert session.posts[0]["json"]["receipt_id"] == "receipt-789"
