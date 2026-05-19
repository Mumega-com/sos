"""Etsy Order Closure.

Consumes ``asset.forged`` events and calls Kasra's closure bridge to complete
the Etsy delivery step. This service sends only fulfillment metadata to the
closure bridge; customer personalization text is intentionally not forwarded.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import redis
import requests

from sos.bus import envelope as bus_envelope
from sos.kernel.information_event import InformationEvent, project_event_stream, safe_summary
from sos.kernel.settings import get_settings

log = logging.getLogger("sos.etsy.order_closure")

DEFAULT_PROJECT = "sos"
DEFAULT_TENANT = "sos"
DEFAULT_TARGET_AGENT = "hermes"
DEFAULT_GROUP = "etsy-order-closure"
DEFAULT_CONSUMER = "order-closure"
DEFAULT_EVENT_TYPE = "asset.forged"
DEFAULT_OUTPUT_EVENT_TYPE = "order.closed"
DEFAULT_PROCESSING_TTL_S = 900
DEFAULT_COMPLETE_TTL_S = 86400 * 90


class RedisLike(Protocol):
    def xadd(self, stream: str, fields: dict[str, str], **kwargs: Any) -> str:
        ...

    def publish(self, channel: str, message: str) -> int:
        ...

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        ...

    def delete(self, *names: str) -> Any:
        ...


class OrderCloser(Protocol):
    name: str

    def close_order(self, request: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class OrderClosureConfig:
    project: str = DEFAULT_PROJECT
    tenant: str = DEFAULT_TENANT
    target_agent: str = DEFAULT_TARGET_AGENT
    group_name: str = DEFAULT_GROUP
    consumer_name: str = DEFAULT_CONSUMER
    block_ms: int = 5000
    count: int = 10
    processing_ttl_s: int = DEFAULT_PROCESSING_TTL_S
    complete_ttl_s: int = DEFAULT_COMPLETE_TTL_S
    closure_url: str = ""
    closure_token: str = ""

    @classmethod
    def from_env(cls) -> "OrderClosureConfig":
        return cls(
            project=os.environ.get("ETSY_ORDER_CLOSURE_PROJECT", DEFAULT_PROJECT),
            tenant=os.environ.get("ETSY_ORDER_CLOSURE_TENANT", DEFAULT_TENANT),
            target_agent=os.environ.get("ETSY_ORDER_CLOSURE_TARGET_AGENT", DEFAULT_TARGET_AGENT),
            group_name=os.environ.get("ETSY_ORDER_CLOSURE_GROUP", DEFAULT_GROUP),
            consumer_name=os.environ.get("ETSY_ORDER_CLOSURE_CONSUMER", DEFAULT_CONSUMER),
            block_ms=int(os.environ.get("ETSY_ORDER_CLOSURE_BLOCK_MS", "5000")),
            count=int(os.environ.get("ETSY_ORDER_CLOSURE_COUNT", "10")),
            processing_ttl_s=int(os.environ.get("ETSY_ORDER_CLOSURE_PROCESSING_TTL", str(DEFAULT_PROCESSING_TTL_S))),
            complete_ttl_s=int(os.environ.get("ETSY_ORDER_CLOSURE_COMPLETE_TTL", str(DEFAULT_COMPLETE_TTL_S))),
            closure_url=(
                os.environ.get("ETSY_ORDER_CLOSURE_URL")
                or os.environ.get("KASRA_ORDER_CLOSURE_URL")
                or os.environ.get("ORDER_CLOSURE_URL", "")
            ),
            closure_token=(
                os.environ.get("ETSY_ORDER_CLOSURE_TOKEN")
                or os.environ.get("KASRA_ORDER_CLOSURE_TOKEN")
                or os.environ.get("ORDER_CLOSURE_TOKEN", "")
            ),
        )

    def redis_url(self) -> str:
        return get_settings().redis.resolved_url


class OrderClosureError(RuntimeError):
    """Raised when the external order closure bridge rejects the request."""


class HttpOrderClosureClient:
    """HTTP contract expected from Kasra's Etsy order closure logic."""

    name = "http-order-closure"

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0, session: Any | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()
        if not self.base_url:
            raise OrderClosureError("ETSY_ORDER_CLOSURE_URL, KASRA_ORDER_CLOSURE_URL, or ORDER_CLOSURE_URL is required.")
        if not self.token:
            raise OrderClosureError("ETSY_ORDER_CLOSURE_TOKEN, KASRA_ORDER_CLOSURE_TOKEN, or ORDER_CLOSURE_TOKEN is required.")

    def close_order(self, request: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/close-order",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            json=request,
            timeout=self.timeout,
        )
        if getattr(response, "status_code", 200) >= 400:
            raise OrderClosureError(f"Order closure failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise OrderClosureError("Order closure response was not a JSON object.")
        return payload


class OrderClosureEmitter:
    def __init__(self, redis_client: RedisLike, *, project: str, tenant: str, target_agent: str):
        self.redis = redis_client
        self.project = project
        self.tenant = tenant
        self.target_agent = target_agent

    def emit_order_closed(self, payload: dict[str, Any]) -> dict[str, str]:
        receipt_id = str(payload.get("receipt_id") or "")
        summary = safe_summary(f"Order closed: Etsy receipt {receipt_id} delivery sent")
        event = InformationEvent(
            event_id=str(uuid4()),
            project=self.project,
            tenant=self.tenant,
            actor="etsy-order-closure",
            event_type=DEFAULT_OUTPUT_EVENT_TYPE,
            summary=summary,
            visibility="internal",
            entity_type="etsy_receipt",
            entity_id=receipt_id,
            payload=payload,
        )
        event_id = self.redis.xadd(project_event_stream(self.project), event.to_redis_fields(), maxlen=5000, approximate=True)

        agent_stream = f"sos:stream:project:{self.project}:agent:{self.target_agent}"
        agent_fields = bus_envelope.build(
            msg_type=DEFAULT_OUTPUT_EVENT_TYPE,
            source="agent:etsy-order-closure",
            target=f"agent:{self.target_agent}",
            text=summary,
            project=self.project,
            extras={
                "event_type": DEFAULT_OUTPUT_EVENT_TYPE,
                "entity_type": "etsy_receipt",
                "entity_id": receipt_id,
                "payload": payload,
            },
        )
        agent_id = self.redis.xadd(agent_stream, agent_fields, maxlen=5000, approximate=True)
        self.redis.publish(f"sos:channel:project:{self.project}:agent:{self.target_agent}", json.dumps(agent_fields))
        return {"event_stream_id": event_id, "agent_stream_id": agent_id}


class OrderClosureService:
    def __init__(
        self,
        *,
        redis_client: RedisLike,
        closer: OrderCloser,
        config: OrderClosureConfig,
        emitter: OrderClosureEmitter | None = None,
    ):
        self.redis = redis_client
        self.closer = closer
        self.config = config
        self.emitter = emitter or OrderClosureEmitter(
            redis_client,
            project=config.project,
            tenant=config.tenant,
            target_agent=config.target_agent,
        )

    def process_event(self, *, stream: str, entry_id: str, fields: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        event_type = str(fields.get("event_type") or fields.get("type") or "")
        if event_type != DEFAULT_EVENT_TYPE:
            return {"ok": True, "skipped": True, "reason": "event_type"}

        payload = _decode_payload(fields.get("payload"))
        receipt_id = str(payload.get("receipt_id") or fields.get("entity_id") or "").strip()
        if not receipt_id:
            return {"ok": True, "skipped": True, "reason": "missing_receipt_id"}

        if not dry_run and not self._claim_processing(receipt_id):
            return {"ok": True, "skipped": True, "reason": "duplicate"}

        try:
            result = close_order_from_asset_payload(
                payload,
                closer=self.closer,
                project=self.config.project,
                dry_run=dry_run,
            )
            if not dry_run:
                self.emitter.emit_order_closed(result["payload"])
                self._mark_complete(receipt_id)
            return {"ok": True, "closed": True, **result}
        except Exception:
            if not dry_run:
                self._release_processing(receipt_id)
            raise

    def _claim_processing(self, receipt_id: str) -> bool:
        key = f"sos:etsy:order_closure:{self.config.project}:{receipt_id}"
        return bool(self.redis.set(key, "processing", nx=True, ex=self.config.processing_ttl_s))

    def _mark_complete(self, receipt_id: str) -> None:
        key = f"sos:etsy:order_closure:{self.config.project}:{receipt_id}"
        self.redis.set(key, "complete", ex=self.config.complete_ttl_s)

    def _release_processing(self, receipt_id: str) -> None:
        key = f"sos:etsy:order_closure:{self.config.project}:{receipt_id}"
        self.redis.delete(key)


def close_order_from_asset_payload(
    payload: dict[str, Any],
    *,
    closer: OrderCloser,
    project: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    request = closure_request_from_asset_payload(payload, project=project)
    closure_response = {"status": "dry_run"} if dry_run else closer.close_order(request)
    status = str(closure_response.get("status") or closure_response.get("closure_status") or "closed")
    reference = str(
        closure_response.get("closure_id")
        or closure_response.get("reference")
        or closure_response.get("etsy_receipt_id")
        or request["receipt_id"]
    )
    output_payload = {
        "source": "etsy-order-closure",
        "shop_id": request["shop_id"],
        "receipt_id": request["receipt_id"],
        "asset_key": request["asset_key"],
        "fulfillment_url": request["fulfillment_url"],
        "closure_status": status,
        "closure_reference": reference,
        "closure_method": closer.name,
        "pii_excluded": True,
    }
    return {
        "receipt_id": request["receipt_id"],
        "closure_status": status,
        "closure_reference": reference,
        "payload": output_payload,
        "request": request,
    }


def closure_request_from_asset_payload(payload: dict[str, Any], *, project: str) -> dict[str, Any]:
    receipt_id = str(payload.get("receipt_id") or "").strip()
    shop_id = str(payload.get("shop_id") or "").strip()
    fulfillment_url = str(payload.get("fulfillment_url") or "").strip()
    asset_key = str(payload.get("asset_key") or "").strip()
    content_type = str(payload.get("content_type") or "").strip()
    if not receipt_id:
        raise ValueError("asset.forged payload missing receipt_id.")
    if not shop_id:
        raise ValueError("asset.forged payload missing shop_id.")
    if not fulfillment_url:
        raise ValueError("asset.forged payload missing fulfillment_url.")
    return {
        "project": project,
        "shop_id": shop_id,
        "receipt_id": receipt_id,
        "asset_key": asset_key,
        "content_type": content_type,
        "fulfillment_url": fulfillment_url,
        "delivery_note": f"Your personalized digital item is ready: {fulfillment_url}",
        "pii_excluded": True,
    }


def run_once(*, redis_client: Any, service: OrderClosureService, config: OrderClosureConfig, dry_run: bool = False) -> dict[str, Any]:
    stream = project_event_stream(config.project)
    _ensure_group(redis_client, stream, config.group_name)
    messages = redis_client.xreadgroup(
        groupname=config.group_name,
        consumername=config.consumer_name,
        streams={stream: ">"},
        count=config.count,
        block=config.block_ms,
    )
    processed = 0
    closed = 0
    skipped = 0
    for stream_name, entries in messages or []:
        stream_str = _decode_redis_value(stream_name)
        for entry_id, fields in entries:
            entry_id_str = _decode_redis_value(entry_id)
            decoded_fields = {_decode_redis_value(key): _decode_redis_value(value) for key, value in fields.items()}
            result = service.process_event(stream=stream_str, entry_id=entry_id_str, fields=decoded_fields, dry_run=dry_run)
            redis_client.xack(stream_str, config.group_name, entry_id_str)
            processed += 1
            if result.get("closed"):
                closed += 1
            elif result.get("skipped"):
                skipped += 1
    return {"ok": True, "processed": processed, "closed": closed, "skipped": skipped, "dry_run": dry_run}


def watch_loop(config: OrderClosureConfig, *, dry_run: bool = False) -> None:
    redis_client = redis.from_url(config.redis_url(), decode_responses=True, socket_timeout=_redis_socket_timeout(config))
    closer = DryRunOrderCloser() if dry_run else HttpOrderClosureClient(config.closure_url, config.closure_token)
    service = OrderClosureService(redis_client=redis_client, closer=closer, config=config)
    while True:
        try:
            result = run_once(redis_client=redis_client, service=service, config=config, dry_run=dry_run)
            log.info("etsy order closure tick", extra={"result": result})
        except Exception as exc:  # noqa: BLE001
            log.exception("etsy order closure crashed: %s", exc)
            time.sleep(5)


class DryRunOrderCloser:
    name = "dry-run"

    def close_order(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"status": "dry_run", "reference": request["receipt_id"]}


def _ensure_group(redis_client: Any, stream: str, group_name: str) -> None:
    try:
        redis_client.xgroup_create(stream, group_name, id="0-0", mkstream=True)
    except Exception as exc:  # noqa: BLE001
        if "BUSYGROUP" not in str(exc):
            raise


def _redis_socket_timeout(config: OrderClosureConfig) -> float:
    return max(10.0, (config.block_ms / 1000.0) + 5.0)


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        decoded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _decode_redis_value(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Close Etsy orders after hosted asset fulfillment.")
    parser.add_argument("--once", action="store_true", help="Process one Redis consumer-group batch and exit.")
    parser.add_argument("--watch", action="store_true", help="Run forever.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Kasra's order closure bridge or emit closure events.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = OrderClosureConfig.from_env()
    redis_client = redis.from_url(config.redis_url(), decode_responses=True, socket_timeout=_redis_socket_timeout(config))
    closer = DryRunOrderCloser() if args.dry_run else HttpOrderClosureClient(config.closure_url, config.closure_token)
    service = OrderClosureService(redis_client=redis_client, closer=closer, config=config)
    if args.once or not args.watch:
        print(json.dumps(run_once(redis_client=redis_client, service=service, config=config, dry_run=args.dry_run), indent=2))
        return 0
    watch_loop(config, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
