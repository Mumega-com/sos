from __future__ import annotations

import json
from typing import Any

from sos.services.etsy.asset_forge import (
    AssetForge,
    AssetForgeConfig,
    HttpR2BridgeUploader,
    forge_asset_from_payload,
    render_personalized_svg,
    select_personalization,
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


class _Uploader:
    name = "test-uploader"

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    def upload(self, *, key: str, content_type: str, body: bytes, metadata: dict[str, str]) -> str:
        self.uploads.append({"key": key, "content_type": content_type, "body": body, "metadata": metadata})
        return f"https://r2.example/{key}"


class _Response:
    status_code = 200

    def json(self) -> dict[str, str]:
        return {"fulfillment_url": "https://r2.example/etsy/shop/r-1/v1.svg"}


class _Session:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
        self.posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response()


def _payload() -> dict[str, Any]:
    return {
        "source": "etsy",
        "shop_id": "shop-123",
        "receipt_id": "receipt-789",
        "confidence": "high",
        "fields": [
            {"label": "Color", "value": "forest green", "source_path": "receipt.transactions[0]", "confidence": 0.88},
            {"label": "Name for artwork", "value": "Hadi <Owner>", "source_path": "receipt.transactions[0]", "confidence": 0.88},
        ],
        "pii_excluded": True,
    }


def test_svg_asset_uses_safe_key_and_escaped_personalization() -> None:
    selection = select_personalization(_payload())
    asset = render_personalized_svg(_payload(), selection=selection, project="sos")

    body = asset.body.decode("utf-8")
    assert asset.key == "etsy/shop-123/receipt-789/v1.svg"
    assert "Hadi &lt;Owner&gt;" in body
    assert "Hadi <Owner>" not in body
    assert "Hadi" not in asset.key
    assert asset.content_type == "image/svg+xml"
    assert asset.metadata["pii_excluded"] == "true"


def test_forge_asset_uploads_and_returns_public_payload() -> None:
    uploader = _Uploader()

    result = forge_asset_from_payload(_payload(), uploader=uploader, project="sos")

    assert result["fulfillment_url"] == "https://r2.example/etsy/shop-123/receipt-789/v1.svg"
    assert uploader.uploads[0]["key"] == "etsy/shop-123/receipt-789/v1.svg"
    assert uploader.uploads[0]["content_type"] == "image/svg+xml"
    assert result["payload"]["field_label"] == "Name for artwork"
    assert result["payload"]["personalization_value"] == "Hadi <Owner>"
    assert result["payload"]["pii_excluded"] is True


def test_asset_forge_processes_personalization_event_and_emits_asset_forged() -> None:
    redis = _Redis()
    forge = AssetForge(
        redis_client=redis,
        uploader=_Uploader(),
        config=AssetForgeConfig(project="sos", tenant="sos", target_agent="hermes"),
    )
    fields = {
        "event_type": "personalization.detected",
        "entity_id": "receipt-789",
        "payload": json.dumps(_payload()),
    }

    result = forge.process_event(stream="sos:stream:project:sos:events", entry_id="1-0", fields=fields)

    assert result["forged"] is True
    assert redis.xadds[0][0] == "sos:stream:project:sos:events"
    assert redis.xadds[0][1]["event_type"] == "asset.forged"
    assert redis.xadds[1][0] == "sos:stream:project:sos:agent:hermes"
    assert redis.xadds[1][1]["type"] == "asset.forged"
    assert redis.values["sos:etsy:asset_forge:sos:receipt-789"] == "complete"


def test_asset_forge_ignores_non_personalization_events() -> None:
    redis = _Redis()
    uploader = _Uploader()
    forge = AssetForge(
        redis_client=redis,
        uploader=uploader,
        config=AssetForgeConfig(project="sos", tenant="sos", target_agent="hermes"),
    )

    result = forge.process_event(
        stream="sos:stream:project:sos:events",
        entry_id="1-0",
        fields={"event_type": "etsy.order.paid", "payload": "{}"},
    )

    assert result == {"ok": True, "skipped": True, "reason": "event_type"}
    assert uploader.uploads == []
    assert redis.xadds == []


def test_asset_forge_dedupes_receipt_before_upload() -> None:
    redis = _Redis()
    redis.set("sos:etsy:asset_forge:sos:receipt-789", "complete")
    uploader = _Uploader()
    forge = AssetForge(
        redis_client=redis,
        uploader=uploader,
        config=AssetForgeConfig(project="sos", tenant="sos", target_agent="hermes"),
    )

    result = forge.process_event(
        stream="sos:stream:project:sos:events",
        entry_id="1-0",
        fields={"event_type": "personalization.detected", "entity_id": "receipt-789", "payload": json.dumps(_payload())},
    )

    assert result == {"ok": True, "skipped": True, "reason": "duplicate"}
    assert uploader.uploads == []
    assert redis.xadds == []


def test_http_r2_bridge_uploader_contract() -> None:
    session = _Session()
    uploader = HttpR2BridgeUploader("https://bridge.example", "token-123", session=session)

    url = uploader.upload(
        key="etsy/shop/receipt/v1.svg",
        content_type="image/svg+xml",
        body=b"<svg/>",
        metadata={"receipt_id": "receipt"},
    )

    assert url == "https://r2.example/etsy/shop/r-1/v1.svg"
    assert session.posts[0]["url"] == "https://bridge.example/upload"
    assert session.posts[0]["headers"]["Authorization"] == "Bearer token-123"
    assert session.posts[0]["json"]["key"] == "etsy/shop/receipt/v1.svg"
    assert session.posts[0]["json"]["body_base64"] == "PHN2Zy8+"
