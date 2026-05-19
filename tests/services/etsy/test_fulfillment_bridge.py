from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sos.services.etsy.fulfillment_bridge import (
    BridgeState,
    EtsyBridgeConfig,
    EtsyClient,
    SosBusEmitter,
    is_paid_receipt,
    nutritional_signal_for_receipt,
    personalization_signal_for_receipt,
    poll_once,
)
from sos.services.etsy.personalization import parse_personalization


class _Response:
    def __init__(self, payload: Any, status_code: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload


class _Session:
    def __init__(self, payload: Any):
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any], timeout: float) -> _Response:
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return _Response(self.payload)

    def post(self, url: str, *, data: dict[str, Any], timeout: float) -> _Response:
        return _Response({"access_token": "fresh-token"})


class _Redis:
    def __init__(self) -> None:
        self.xadds: list[tuple[str, dict[str, str]]] = []
        self.publishes: list[tuple[str, str]] = []
        self.claimed: set[str] = set()

    def xadd(self, stream: str, fields: dict[str, str], **kwargs: Any) -> str:
        self.xadds.append((stream, fields))
        return f"{len(self.xadds)}-0"

    def publish(self, channel: str, message: str) -> int:
        self.publishes.append((channel, message))
        return 1

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and name in self.claimed:
            return False
        self.claimed.add(name)
        return True


def _config(tmp_path: Path) -> EtsyBridgeConfig:
    return EtsyBridgeConfig(
        shop_id="shop-123",
        api_key="api-key",
        access_token="token",
        project="sos",
        tenant="sos",
        target_agent="hermes",
        receipt_limit=10,
        state_path=tmp_path / "etsy-state.json",
    )


def _paid_receipt(receipt_id: str = "r-1", created: int = 1779020000) -> dict[str, Any]:
    return {
        "receipt_id": receipt_id,
        "status": "paid",
        "was_paid": True,
        "create_timestamp": created,
        "grandtotal": {"amount": 2500, "divisor": 100, "currency_code": "USD"},
        "transactions": [
            {"listing_id": 42, "title": "Personalized planner", "quantity": 1},
        ],
        "buyer_email": "buyer@example.com",
        "formatted_address": "Do not emit",
    }


def _personalized_receipt(receipt_id: str = "r-p1") -> dict[str, Any]:
    receipt = _paid_receipt(receipt_id)
    receipt["transactions"] = [
        {
            "listing_id": 84,
            "title": "Custom nursery print",
            "personalization": "Name for the artwork: [Hadi]\nColor: forest green",
            "variations": [
                {"formatted_name": "Baby Name", "formatted_value": "Soren"},
                {"formatted_name": "Frame", "formatted_value": "Oak"},
            ],
        }
    ]
    receipt["buyer_message"] = "Please use quote: Dream awake"
    return receipt


def test_paid_receipt_detection_accepts_status_or_flag() -> None:
    assert is_paid_receipt({"status": "paid"}) is True
    assert is_paid_receipt({"was_paid": True}) is True
    assert is_paid_receipt({"status": "payment processing"}) is False


def test_nutritional_signal_excludes_buyer_pii(tmp_path: Path) -> None:
    signal = nutritional_signal_for_receipt(_paid_receipt(), config=_config(tmp_path))

    encoded = json.dumps(signal)
    assert signal["payload"]["pii_excluded"] is True
    assert "buyer@example.com" not in encoded
    assert "Do not emit" not in encoded
    assert signal["payload"]["listing_titles"] == ["Personalized planner"]
    assert signal["payload"]["total"] == "25.00 USD"


def test_personalization_parser_extracts_varied_seller_fields() -> None:
    parsed = parse_personalization(_personalized_receipt())

    values = {(field.label, field.value) for field in parsed.fields}
    assert parsed.detected is True
    assert parsed.confidence == "high"
    assert ("Name For The Artwork", "Hadi") in values
    assert ("Baby Name", "Soren") in values
    assert ("Please Use Quote", "Dream awake") in values
    assert parsed.hermes_reasoning_required is False


def test_personalization_signal_excludes_buyer_contact_data(tmp_path: Path) -> None:
    signal = personalization_signal_for_receipt(_personalized_receipt(), config=_config(tmp_path))

    assert signal is not None
    encoded = json.dumps(signal)
    assert signal["payload"]["confidence"] == "high"
    assert signal["payload"]["pii_excluded"] is True
    assert "buyer@example.com" not in encoded
    assert "Do not emit" not in encoded


def test_personalization_parser_flags_medium_confidence_for_hermes_reasoning() -> None:
    receipt = _paid_receipt("r-medium")
    receipt["transactions"] = [
        {"listing_id": 90, "title": "Custom print", "customization": "Use the mountain version"}
    ]

    parsed = parse_personalization(receipt)

    assert parsed.detected is True
    assert parsed.confidence == "medium"
    assert parsed.hermes_reasoning_required is True


def test_poll_once_fetches_paid_receipts_and_emits_bus_signal(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = _Session({"results": [_paid_receipt("r-1"), {"receipt_id": "r-2", "status": "open"}]})
    client = EtsyClient(config, session=session)
    redis = _Redis()
    emitter = SosBusEmitter(redis, project="sos", tenant="sos", target_agent="hermes")
    state = BridgeState()

    result = poll_once(client=client, emitter=emitter, state=state, config=config)

    assert result["fetched"] == 2
    assert result["emitted"] == 1
    assert session.calls[0]["url"].endswith("/shops/shop-123/receipts")
    assert session.calls[0]["params"]["was_paid"] == "true"
    assert redis.xadds[0][0] == "sos:stream:project:sos:events"
    assert redis.xadds[1][0] == "sos:stream:project:sos:agent:hermes"
    assert redis.xadds[1][1]["type"] == "nutritional_signal"
    assert (tmp_path / "etsy-state.json").exists()


def test_poll_once_emits_personalization_detected_event(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = _Session({"results": [_personalized_receipt("r-p1")]})
    client = EtsyClient(config, session=session)
    redis = _Redis()
    emitter = SosBusEmitter(redis, project="sos", tenant="sos", target_agent="hermes")
    state = BridgeState()

    result = poll_once(client=client, emitter=emitter, state=state, config=config)

    assert result["emitted"] == 1
    assert result["personalization_emitted"] == 1
    assert result["personalization_signals"][0]["payload"]["fields"][0]["value"]
    assert redis.xadds[0][1]["event_type"] == "etsy.order.paid"
    assert redis.xadds[1][1]["type"] == "nutritional_signal"
    assert redis.xadds[2][1]["event_type"] == "personalization.detected"
    assert redis.xadds[3][1]["type"] == "personalization.detected"


def test_poll_once_dedupes_seen_receipts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = _Session({"results": [_paid_receipt("r-1")]})
    client = EtsyClient(config, session=session)
    redis = _Redis()
    emitter = SosBusEmitter(redis, project="sos", tenant="sos", target_agent="hermes")
    state = BridgeState(seen_receipt_ids=["r-1"])

    result = poll_once(client=client, emitter=emitter, state=state, config=config)

    assert result["emitted"] == 0
    assert result["skipped_seen"] == 1
    assert redis.xadds == []


def test_poll_once_uses_redis_claim_to_prevent_overlap_duplicates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = _Session({"results": [_paid_receipt("r-1")]})
    client = EtsyClient(config, session=session)
    redis = _Redis()
    redis.set("sos:etsy:receipt_seen:sos:r-1", "1", nx=True, ex=60)
    emitter = SosBusEmitter(redis, project="sos", tenant="sos", target_agent="hermes")
    state = BridgeState()

    result = poll_once(client=client, emitter=emitter, state=state, config=config)

    assert result["emitted"] == 0
    assert result["skipped_seen"] == 1
    assert redis.xadds == []
