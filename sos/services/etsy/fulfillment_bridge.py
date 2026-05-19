"""Etsy Fulfillment Bridge.

Polls Etsy Open API v3 shop receipts for new paid orders and emits an
internal SOS "nutritional signal" onto the bus. This is deliberately a sensor:
it reads receipts, deduplicates, and notifies the organism. It does not fulfill
orders, upload files, contact buyers, or expose buyer PII.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import uuid4

import redis
import requests

from sos.bus import envelope as bus_envelope
from sos.kernel.information_event import InformationEvent, project_event_stream, safe_summary
from sos.kernel.settings import get_settings
from sos.services.etsy.personalization import (
    personalization_event_payload,
    parse_personalization,
)

log = logging.getLogger("sos.etsy.fulfillment_bridge")

ETSY_BASE_URL = "https://api.etsy.com/v3/application"
DEFAULT_STATE_PATH = Path.home() / ".sos" / "state" / "etsy-fulfillment-bridge.json"
DEFAULT_TARGET_AGENT = "hermes"
DEFAULT_PROJECT = "sos"
DEFAULT_TENANT = "sos"


class HttpSession(Protocol):
    def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any], timeout: float) -> Any:
        ...

    def post(self, url: str, *, data: dict[str, Any], timeout: float) -> Any:
        ...


class RedisLike(Protocol):
    def xadd(self, stream: str, fields: dict[str, str], **kwargs: Any) -> str:
        ...

    def publish(self, channel: str, message: str) -> int:
        ...

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        ...


@dataclass(frozen=True)
class EtsyBridgeConfig:
    shop_id: str
    api_key: str
    access_token: str
    refresh_token: str = ""
    client_id: str = ""
    base_url: str = ETSY_BASE_URL
    project: str = DEFAULT_PROJECT
    tenant: str = DEFAULT_TENANT
    target_agent: str = DEFAULT_TARGET_AGENT
    poll_interval_s: int = 300
    receipt_limit: int = 25
    lookback_s: int = 86400
    state_path: Path = DEFAULT_STATE_PATH

    @classmethod
    def from_env(cls) -> "EtsyBridgeConfig":
        state_path = Path(os.environ.get("ETSY_BRIDGE_STATE_PATH", str(DEFAULT_STATE_PATH)))
        return cls(
            shop_id=_required_env("ETSY_SHOP_ID"),
            api_key=_required_env("ETSY_API_KEY"),
            access_token=_required_env("ETSY_ACCESS_TOKEN"),
            refresh_token=os.environ.get("ETSY_REFRESH_TOKEN", ""),
            client_id=os.environ.get("ETSY_CLIENT_ID") or os.environ.get("ETSY_API_KEY", ""),
            base_url=os.environ.get("ETSY_API_BASE_URL", ETSY_BASE_URL).rstrip("/"),
            project=os.environ.get("ETSY_BRIDGE_PROJECT", DEFAULT_PROJECT),
            tenant=os.environ.get("ETSY_BRIDGE_TENANT", DEFAULT_TENANT),
            target_agent=os.environ.get("ETSY_BRIDGE_TARGET_AGENT", DEFAULT_TARGET_AGENT),
            poll_interval_s=int(os.environ.get("ETSY_BRIDGE_POLL_INTERVAL", "300")),
            receipt_limit=int(os.environ.get("ETSY_BRIDGE_RECEIPT_LIMIT", "25")),
            lookback_s=int(os.environ.get("ETSY_BRIDGE_LOOKBACK_SECONDS", "86400")),
            state_path=state_path,
        )

    def redis_url(self) -> str:
        return get_settings().redis.resolved_url


@dataclass
class BridgeState:
    seen_receipt_ids: list[str] = field(default_factory=list)
    last_seen_created: int = 0
    updated_at: str = ""

    @classmethod
    def load(cls, path: Path) -> "BridgeState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        return cls(
            seen_receipt_ids=[str(item) for item in raw.get("seen_receipt_ids", [])],
            last_seen_created=int(raw.get("last_seen_created") or 0),
            updated_at=str(raw.get("updated_at") or ""),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.seen_receipt_ids = self.seen_receipt_ids[-1000:]
        self.updated_at = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def has_seen(self, receipt_id: str) -> bool:
        return receipt_id in set(self.seen_receipt_ids)

    def mark_seen(self, receipt_id: str, created: int) -> None:
        if receipt_id not in self.seen_receipt_ids:
            self.seen_receipt_ids.append(receipt_id)
        if created > self.last_seen_created:
            self.last_seen_created = created


class EtsyApiError(RuntimeError):
    """Raised when Etsy returns an API error."""

    def __init__(self, message: str, *, status_code: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class EtsyClient:
    def __init__(self, config: EtsyBridgeConfig, session: HttpSession | None = None):
        self.config = config
        self.session = session or requests.Session()
        self.access_token = config.access_token

    def get_paid_receipts(self, *, min_created: int, limit: int) -> list[dict[str, Any]]:
        params = {
            "min_created": min_created,
            "limit": max(1, min(limit, 100)),
            "sort_on": "created",
            "sort_order": "up",
            "was_paid": "true",
        }
        return self._get_receipts(params)

    def _get_receipts(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        url = f"{self.config.base_url}/shops/{self.config.shop_id}/receipts"
        response = self.session.get(url, headers=self._headers(), params=params, timeout=15)
        if getattr(response, "status_code", 200) == 401 and self.config.refresh_token:
            self.refresh_access_token()
            response = self.session.get(url, headers=self._headers(), params=params, timeout=15)
        if getattr(response, "status_code", 200) >= 400:
            raise EtsyApiError(
                f"Etsy receipts API failed with HTTP {response.status_code}",
                status_code=response.status_code,
                retry_after=_int_header(getattr(response, "headers", {}), "Retry-After"),
            )
        payload = response.json()
        if isinstance(payload, dict):
            rows = payload.get("results") or payload.get("receipts") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        return [row for row in rows if isinstance(row, dict)]

    def refresh_access_token(self) -> None:
        if not self.config.refresh_token or not self.config.client_id:
            raise EtsyApiError("Etsy access token expired and refresh credentials are not configured.")
        response = self.session.post(
            "https://api.etsy.com/v3/public/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "refresh_token": self.config.refresh_token,
            },
            timeout=15,
        )
        if getattr(response, "status_code", 200) >= 400:
            raise EtsyApiError(
                f"Etsy token refresh failed with HTTP {response.status_code}",
                status_code=response.status_code,
            )
        payload = response.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise EtsyApiError("Etsy token refresh response did not include access_token.")
        self.access_token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "x-api-key": self.config.api_key,
            "Accept": "application/json",
        }


class SosBusEmitter:
    def __init__(self, redis_client: RedisLike, *, project: str, tenant: str, target_agent: str):
        self.redis = redis_client
        self.project = project
        self.tenant = tenant
        self.target_agent = target_agent

    def claim_receipt(self, receipt_id: str) -> bool:
        """Atomically claim a receipt before emitting.

        The local JSON state protects ordinary restarts. This Redis SET NX guard
        protects the bus from duplicate nutrient events if two bridge instances
        overlap during deploy/restart.
        """
        key = f"sos:etsy:receipt_seen:{self.project}:{receipt_id}"
        result = self.redis.set(key, "1", nx=True, ex=86400 * 90)
        return bool(result)

    def emit_nutritional_signal(self, signal: dict[str, Any]) -> dict[str, str]:
        event = InformationEvent(
            event_id=str(uuid4()),
            project=self.project,
            tenant=self.tenant,
            actor="etsy-fulfillment-bridge",
            event_type="etsy.order.paid",
            summary=signal["summary"],
            visibility="internal",
            entity_type="etsy_receipt",
            entity_id=str(signal["receipt_id"]),
            payload=signal["payload"],
        )
        event_stream = project_event_stream(self.project)
        event_id = self.redis.xadd(event_stream, event.to_redis_fields(), maxlen=5000, approximate=True)

        agent_stream = f"sos:stream:project:{self.project}:agent:{self.target_agent}"
        agent_fields = bus_envelope.build(
            msg_type="nutritional_signal",
            source="agent:etsy-fulfillment-bridge",
            target=f"agent:{self.target_agent}",
            text=signal["summary"],
            project=self.project,
            extras={
                "event_type": "etsy.order.paid",
                "entity_type": "etsy_receipt",
                "entity_id": str(signal["receipt_id"]),
                "payload": signal["payload"],
            },
        )
        agent_id = self.redis.xadd(agent_stream, agent_fields, maxlen=5000, approximate=True)
        self.redis.publish(f"sos:channel:project:{self.project}:agent:{self.target_agent}", json.dumps(agent_fields))
        return {"event_stream_id": event_id, "agent_stream_id": agent_id}

    def emit_personalization_detected(self, signal: dict[str, Any]) -> dict[str, str]:
        event = InformationEvent(
            event_id=str(uuid4()),
            project=self.project,
            tenant=self.tenant,
            actor="etsy-fulfillment-bridge",
            event_type="personalization.detected",
            summary=signal["summary"],
            visibility="internal",
            entity_type="etsy_receipt",
            entity_id=str(signal["receipt_id"]),
            payload=signal["payload"],
        )
        event_stream = project_event_stream(self.project)
        event_id = self.redis.xadd(event_stream, event.to_redis_fields(), maxlen=5000, approximate=True)

        agent_stream = f"sos:stream:project:{self.project}:agent:{self.target_agent}"
        agent_fields = bus_envelope.build(
            msg_type="personalization.detected",
            source="agent:etsy-fulfillment-bridge",
            target=f"agent:{self.target_agent}",
            text=signal["summary"],
            project=self.project,
            extras={
                "event_type": "personalization.detected",
                "entity_type": "etsy_receipt",
                "entity_id": str(signal["receipt_id"]),
                "payload": signal["payload"],
                "reasoning_mode": "hermes",
            },
        )
        agent_id = self.redis.xadd(agent_stream, agent_fields, maxlen=5000, approximate=True)
        self.redis.publish(f"sos:channel:project:{self.project}:agent:{self.target_agent}", json.dumps(agent_fields))
        return {"event_stream_id": event_id, "agent_stream_id": agent_id}


def poll_once(
    *,
    client: EtsyClient,
    emitter: SosBusEmitter,
    state: BridgeState,
    config: EtsyBridgeConfig,
    dry_run: bool = False,
) -> dict[str, Any]:
    min_created = state.last_seen_created or int(time.time()) - config.lookback_s
    receipts = client.get_paid_receipts(min_created=min_created, limit=config.receipt_limit)
    emitted = 0
    personalization_emitted = 0
    skipped_seen = 0
    signals: list[dict[str, Any]] = []
    personalization_signals: list[dict[str, Any]] = []

    for receipt in receipts:
        if not is_paid_receipt(receipt):
            continue
        receipt_id = receipt_id_of(receipt)
        if not receipt_id:
            continue
        created = receipt_created_timestamp(receipt)
        if state.has_seen(receipt_id):
            skipped_seen += 1
            continue
        if not dry_run and not emitter.claim_receipt(receipt_id):
            state.mark_seen(receipt_id, created)
            skipped_seen += 1
            continue
        signal = nutritional_signal_for_receipt(receipt, config=config)
        signals.append(signal)
        if not dry_run:
            emitter.emit_nutritional_signal(signal)
        personalization = personalization_signal_for_receipt(receipt, config=config)
        if personalization:
            personalization_signals.append(personalization)
            if not dry_run:
                emitter.emit_personalization_detected(personalization)
            personalization_emitted += 1
        state.mark_seen(receipt_id, created)
        emitted += 1

    if not dry_run:
        state.save(config.state_path)

    return {
        "ok": True,
        "fetched": len(receipts),
        "emitted": emitted,
        "personalization_emitted": personalization_emitted,
        "skipped_seen": skipped_seen,
        "dry_run": dry_run,
        "last_seen_created": state.last_seen_created,
        "signals": signals,
        "personalization_signals": personalization_signals,
    }


def watch_loop(config: EtsyBridgeConfig, *, dry_run: bool = False) -> None:
    redis_client = redis.from_url(config.redis_url(), decode_responses=True, socket_timeout=5)
    client = EtsyClient(config)
    emitter = SosBusEmitter(redis_client, project=config.project, tenant=config.tenant, target_agent=config.target_agent)
    while True:
        state = BridgeState.load(config.state_path)
        try:
            result = poll_once(client=client, emitter=emitter, state=state, config=config, dry_run=dry_run)
            log.info("etsy fulfillment bridge poll", extra={"result": result})
        except EtsyApiError as exc:
            sleep_s = exc.retry_after or config.poll_interval_s
            log.warning("etsy poll failed: %s; sleeping %ss", exc, sleep_s)
            time.sleep(sleep_s)
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("etsy poll crashed: %s", exc)
        time.sleep(config.poll_interval_s)


def is_paid_receipt(receipt: dict[str, Any]) -> bool:
    status = str(receipt.get("status") or receipt.get("state") or "").strip().lower()
    return bool(receipt.get("was_paid") is True or receipt.get("is_paid") is True or status == "paid")


def receipt_id_of(receipt: dict[str, Any]) -> str:
    return str(receipt.get("receipt_id") or receipt.get("id") or "").strip()


def receipt_created_timestamp(receipt: dict[str, Any]) -> int:
    for key in ("create_timestamp", "created_timestamp", "created", "creation_tsz"):
        value = receipt.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return int(time.time())


def nutritional_signal_for_receipt(receipt: dict[str, Any], *, config: EtsyBridgeConfig) -> dict[str, Any]:
    receipt_id = receipt_id_of(receipt)
    transactions = receipt.get("transactions") if isinstance(receipt.get("transactions"), list) else []
    transaction_rows = [item for item in transactions if isinstance(item, dict)]
    listing_ids = [str(item.get("listing_id")) for item in transaction_rows if item.get("listing_id")]
    listing_titles = [safe_summary(item.get("title"), limit=80) for item in transaction_rows if item.get("title")]
    total = _money_summary(receipt)
    created = receipt_created_timestamp(receipt)
    summary = safe_summary(
        f"Nutritional signal: Etsy paid order {receipt_id} for shop {config.shop_id}"
        + (f" ({total})" if total else "")
    )
    return {
        "receipt_id": receipt_id,
        "summary": summary,
        "payload": {
            "source": "etsy",
            "shop_id": config.shop_id,
            "receipt_id": receipt_id,
            "status": str(receipt.get("status") or "paid"),
            "was_paid": True,
            "created_timestamp": created,
            "updated_timestamp": _optional_int(receipt.get("updated_timestamp") or receipt.get("update_timestamp")),
            "total": total,
            "transaction_count": len(transaction_rows),
            "listing_ids": listing_ids[:20],
            "listing_titles": listing_titles[:20],
            "fulfillment_goal": "zero_click_fulfillment",
            "pii_excluded": True,
        },
    }


def personalization_signal_for_receipt(receipt: dict[str, Any], *, config: EtsyBridgeConfig) -> dict[str, Any] | None:
    result = parse_personalization(receipt)
    if not result.detected:
        return None
    payload = personalization_event_payload(result, shop_id=config.shop_id)
    field_labels = ", ".join(field["label"] for field in payload["fields"][:3])
    summary = safe_summary(
        f"Personalization detected: Etsy receipt {result.receipt_id}"
        + (f" ({field_labels})" if field_labels else "")
    )
    return {
        "receipt_id": result.receipt_id,
        "summary": summary,
        "payload": payload,
    }


def _money_summary(receipt: dict[str, Any]) -> str:
    money = receipt.get("grandtotal") or receipt.get("total_price") or receipt.get("total")
    if isinstance(money, dict):
        amount = _optional_int(money.get("amount"))
        divisor = _optional_int(money.get("divisor")) or 100
        currency = str(money.get("currency_code") or money.get("currency") or "").strip()
        if amount is not None:
            return f"{amount / divisor:.2f} {currency}".strip()
    if isinstance(money, (str, int, float)) and str(money).strip():
        currency = str(receipt.get("currency_code") or receipt.get("currency") or "").strip()
        return f"{money} {currency}".strip()
    return ""


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_header(headers: Any, name: str) -> int | None:
    try:
        return int(headers.get(name))
    except Exception:
        return None


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for Etsy Fulfillment Bridge.")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll Etsy paid receipts and emit SOS nutritional signals.")
    parser.add_argument("--once", action="store_true", help="Run one poll and exit.")
    parser.add_argument("--watch", action="store_true", help="Run forever.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and dedupe without emitting bus writes.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = EtsyBridgeConfig.from_env()
    if args.once or not args.watch:
        redis_client = redis.from_url(config.redis_url(), decode_responses=True, socket_timeout=5)
        client = EtsyClient(config)
        emitter = SosBusEmitter(redis_client, project=config.project, tenant=config.tenant, target_agent=config.target_agent)
        state = BridgeState.load(config.state_path)
        print(json.dumps(poll_once(client=client, emitter=emitter, state=state, config=config, dry_run=args.dry_run), indent=2))
        return 0
    watch_loop(config, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
