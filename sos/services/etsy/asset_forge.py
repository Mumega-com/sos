"""Etsy Asset Forge.

Consumes ``personalization.detected`` project events, renders a deterministic
digital good, uploads it through the R2 hosting bridge, and emits
``asset.forged``. The forge never puts customer text in object keys.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import redis
import requests

from sos.bus import envelope as bus_envelope
from sos.kernel.information_event import InformationEvent, project_event_stream, safe_summary
from sos.kernel.settings import get_settings

log = logging.getLogger("sos.etsy.asset_forge")

DEFAULT_PROJECT = "sos"
DEFAULT_TENANT = "sos"
DEFAULT_TARGET_AGENT = "hermes"
DEFAULT_GROUP = "etsy-asset-forge"
DEFAULT_CONSUMER = "asset-forge"
DEFAULT_EVENT_TYPE = "personalization.detected"
DEFAULT_OUTPUT_EVENT_TYPE = "asset.forged"
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


class AssetUploader(Protocol):
    name: str

    def upload(
        self,
        *,
        key: str,
        content_type: str,
        body: bytes,
        metadata: dict[str, str],
    ) -> str:
        ...


@dataclass(frozen=True)
class AssetForgeConfig:
    project: str = DEFAULT_PROJECT
    tenant: str = DEFAULT_TENANT
    target_agent: str = DEFAULT_TARGET_AGENT
    group_name: str = DEFAULT_GROUP
    consumer_name: str = DEFAULT_CONSUMER
    block_ms: int = 5000
    count: int = 10
    processing_ttl_s: int = DEFAULT_PROCESSING_TTL_S
    complete_ttl_s: int = DEFAULT_COMPLETE_TTL_S
    bridge_url: str = ""
    bridge_token: str = ""
    output_dir: Path = Path.home() / ".sos" / "etsy-assets"

    @classmethod
    def from_env(cls) -> "AssetForgeConfig":
        return cls(
            project=os.environ.get("ETSY_ASSET_FORGE_PROJECT", DEFAULT_PROJECT),
            tenant=os.environ.get("ETSY_ASSET_FORGE_TENANT", DEFAULT_TENANT),
            target_agent=os.environ.get("ETSY_ASSET_FORGE_TARGET_AGENT", DEFAULT_TARGET_AGENT),
            group_name=os.environ.get("ETSY_ASSET_FORGE_GROUP", DEFAULT_GROUP),
            consumer_name=os.environ.get("ETSY_ASSET_FORGE_CONSUMER", DEFAULT_CONSUMER),
            block_ms=int(os.environ.get("ETSY_ASSET_FORGE_BLOCK_MS", "5000")),
            count=int(os.environ.get("ETSY_ASSET_FORGE_COUNT", "10")),
            processing_ttl_s=int(os.environ.get("ETSY_ASSET_FORGE_PROCESSING_TTL", str(DEFAULT_PROCESSING_TTL_S))),
            complete_ttl_s=int(os.environ.get("ETSY_ASSET_FORGE_COMPLETE_TTL", str(DEFAULT_COMPLETE_TTL_S))),
            bridge_url=os.environ.get("ETSY_R2_BRIDGE_URL") or os.environ.get("R2_BRIDGE_URL", ""),
            bridge_token=os.environ.get("ETSY_R2_BRIDGE_TOKEN") or os.environ.get("R2_BRIDGE_TOKEN", ""),
            output_dir=Path(os.environ.get("ETSY_ASSET_FORGE_OUTPUT_DIR", str(Path.home() / ".sos" / "etsy-assets"))),
        )

    def redis_url(self) -> str:
        return get_settings().redis.resolved_url


@dataclass(frozen=True)
class PersonalizationSelection:
    label: str
    value: str
    confidence: str


@dataclass(frozen=True)
class GeneratedAsset:
    key: str
    content_type: str
    body: bytes
    metadata: dict[str, str] = field(default_factory=dict)


class R2BridgeUploadError(RuntimeError):
    """Raised when the R2 hosting bridge rejects an upload."""


class HttpR2BridgeUploader:
    """HTTP contract expected from Kasra's R2 hosting bridge."""

    name = "http-r2-bridge"

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0, session: Any | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()
        if not self.base_url:
            raise R2BridgeUploadError("ETSY_R2_BRIDGE_URL or R2_BRIDGE_URL is required.")
        if not self.token:
            raise R2BridgeUploadError("ETSY_R2_BRIDGE_TOKEN or R2_BRIDGE_TOKEN is required.")

    def upload(
        self,
        *,
        key: str,
        content_type: str,
        body: bytes,
        metadata: dict[str, str],
    ) -> str:
        response = self.session.post(
            f"{self.base_url}/upload",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "SOS-Asset-Forge/1.0",
            },
            json={
                "key": key,
                "content_type": content_type,
                "body_base64": base64.b64encode(body).decode("ascii"),
                "metadata": metadata,
            },
            timeout=self.timeout,
        )
        if getattr(response, "status_code", 200) >= 400:
            raise R2BridgeUploadError(f"R2 bridge upload failed with HTTP {response.status_code}")
        payload = response.json()
        url = str(payload.get("fulfillment_url") or payload.get("url") or "").strip()
        if not url:
            raise R2BridgeUploadError("R2 bridge response did not include fulfillment_url or url.")
        return url


class LocalDraftUploader:
    """Dry-run uploader for development only. It is not customer fulfillment."""

    name = "local-draft"

    def __init__(self, root: Path):
        self.root = root

    def upload(
        self,
        *,
        key: str,
        content_type: str,
        body: bytes,
        metadata: dict[str, str],
    ) -> str:
        path = (self.root / key).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path.as_uri()


class AssetForgeEmitter:
    def __init__(self, redis_client: RedisLike, *, project: str, tenant: str, target_agent: str):
        self.redis = redis_client
        self.project = project
        self.tenant = tenant
        self.target_agent = target_agent

    def emit_asset_forged(self, payload: dict[str, Any]) -> dict[str, str]:
        receipt_id = str(payload.get("receipt_id") or "")
        summary = safe_summary(f"Asset forged: Etsy receipt {receipt_id} hosted digital good is ready")
        event = InformationEvent(
            event_id=str(uuid4()),
            project=self.project,
            tenant=self.tenant,
            actor="etsy-asset-forge",
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
            source="agent:etsy-asset-forge",
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


class AssetForge:
    def __init__(
        self,
        *,
        redis_client: RedisLike,
        uploader: AssetUploader,
        config: AssetForgeConfig,
        emitter: AssetForgeEmitter | None = None,
    ):
        self.redis = redis_client
        self.uploader = uploader
        self.config = config
        self.emitter = emitter or AssetForgeEmitter(
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
            result = forge_asset_from_payload(
                payload,
                uploader=self.uploader,
                project=self.config.project,
                dry_run=dry_run,
            )
            if not dry_run:
                self.emitter.emit_asset_forged(result["payload"])
                self._mark_complete(receipt_id)
            return {"ok": True, "forged": True, **result}
        except Exception:
            if not dry_run:
                self._release_processing(receipt_id)
            raise

    def _claim_processing(self, receipt_id: str) -> bool:
        key = f"sos:etsy:asset_forge:{self.config.project}:{receipt_id}"
        return bool(self.redis.set(key, "processing", nx=True, ex=self.config.processing_ttl_s))

    def _mark_complete(self, receipt_id: str) -> None:
        key = f"sos:etsy:asset_forge:{self.config.project}:{receipt_id}"
        self.redis.set(key, "complete", ex=self.config.complete_ttl_s)

    def _release_processing(self, receipt_id: str) -> None:
        key = f"sos:etsy:asset_forge:{self.config.project}:{receipt_id}"
        self.redis.delete(key)


def forge_asset_from_payload(
    payload: dict[str, Any],
    *,
    uploader: AssetUploader,
    project: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    selection = select_personalization(payload)
    asset = render_personalized_svg(payload, selection=selection, project=project)
    fulfillment_url = ""
    if not dry_run:
        fulfillment_url = uploader.upload(
            key=asset.key,
            content_type=asset.content_type,
            body=asset.body,
            metadata=asset.metadata,
        )
    output_payload = {
        "source": "etsy-asset-forge",
        "shop_id": str(payload.get("shop_id") or ""),
        "receipt_id": str(payload.get("receipt_id") or ""),
        "field_label": selection.label,
        "personalization_value": safe_summary(selection.value, limit=120),
        "asset_key": asset.key,
        "content_type": asset.content_type,
        "fulfillment_url": fulfillment_url,
        "uploader": uploader.name,
        "pii_excluded": True,
    }
    return {
        "asset_key": asset.key,
        "content_type": asset.content_type,
        "body_size": len(asset.body),
        "fulfillment_url": fulfillment_url,
        "payload": output_payload,
    }


def select_personalization(payload: dict[str, Any]) -> PersonalizationSelection:
    fields = payload.get("fields") if isinstance(payload.get("fields"), list) else []
    candidates = [field for field in fields if isinstance(field, dict) and str(field.get("value") or "").strip()]
    if not candidates:
        raise ValueError("personalization.detected payload contains no usable fields.")
    preferred = next(
        (
            field
            for field in candidates
            if "name" in str(field.get("label") or "").lower()
        ),
        candidates[0],
    )
    return PersonalizationSelection(
        label=safe_summary(preferred.get("label"), limit=80) or "Personalization",
        value=safe_summary(preferred.get("value"), limit=160),
        confidence=str(payload.get("confidence") or "unknown"),
    )


def render_personalized_svg(payload: dict[str, Any], *, selection: PersonalizationSelection, project: str) -> GeneratedAsset:
    receipt_id = _safe_key_part(str(payload.get("receipt_id") or "unknown"))
    shop_id = _safe_key_part(str(payload.get("shop_id") or "unknown"))
    key = f"etsy/{shop_id}/{receipt_id}/v1.svg"
    title = html.escape(selection.value)
    subtitle = html.escape(selection.label)
    receipt_text = html.escape(receipt_id)
    body = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="2400" viewBox="0 0 1800 2400">
  <rect width="1800" height="2400" fill="#f7efe2"/>
  <circle cx="900" cy="820" r="560" fill="#d9c2a3" opacity="0.55"/>
  <circle cx="900" cy="820" r="420" fill="none" stroke="#385a4b" stroke-width="18"/>
  <path d="M360 1600 C600 1450 1200 1450 1440 1600" fill="none" stroke="#385a4b" stroke-width="12"/>
  <text x="900" y="830" text-anchor="middle" font-family="Georgia, serif" font-size="150" fill="#173b31">{title}</text>
  <text x="900" y="1000" text-anchor="middle" font-family="Georgia, serif" font-size="48" fill="#5d4d3d">{subtitle}</text>
  <text x="900" y="2060" text-anchor="middle" font-family="Courier New, monospace" font-size="28" fill="#7a6a5a">forged by SOS Asset Forge - receipt {receipt_text}</text>
</svg>
"""
    return GeneratedAsset(
        key=key,
        content_type="image/svg+xml",
        body=body.encode("utf-8"),
        metadata={
            "project": project,
            "source": "etsy-asset-forge",
            "shop_id": shop_id,
            "receipt_id": receipt_id,
            "pii_excluded": "true",
        },
    )


def run_once(*, redis_client: Any, forge: AssetForge, config: AssetForgeConfig, dry_run: bool = False) -> dict[str, Any]:
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
    forged = 0
    skipped = 0
    for stream_name, entries in messages or []:
        stream_str = _decode_redis_value(stream_name)
        for entry_id, fields in entries:
            entry_id_str = _decode_redis_value(entry_id)
            decoded_fields = {_decode_redis_value(key): _decode_redis_value(value) for key, value in fields.items()}
            result = forge.process_event(stream=stream_str, entry_id=entry_id_str, fields=decoded_fields, dry_run=dry_run)
            redis_client.xack(stream_str, config.group_name, entry_id_str)
            processed += 1
            if result.get("forged"):
                forged += 1
            elif result.get("skipped"):
                skipped += 1
    return {"ok": True, "processed": processed, "forged": forged, "skipped": skipped, "dry_run": dry_run}


def watch_loop(config: AssetForgeConfig, *, dry_run: bool = False) -> None:
    redis_client = redis.from_url(config.redis_url(), decode_responses=True, socket_timeout=_redis_socket_timeout(config))
    uploader: AssetUploader = LocalDraftUploader(config.output_dir) if dry_run else HttpR2BridgeUploader(config.bridge_url, config.bridge_token)
    forge = AssetForge(redis_client=redis_client, uploader=uploader, config=config)
    while True:
        try:
            result = run_once(redis_client=redis_client, forge=forge, config=config, dry_run=dry_run)
            log.info("etsy asset forge tick", extra={"result": result})
        except Exception as exc:  # noqa: BLE001
            log.exception("etsy asset forge crashed: %s", exc)
            time.sleep(5)


def _ensure_group(redis_client: Any, stream: str, group_name: str) -> None:
    try:
        redis_client.xgroup_create(stream, group_name, id="0-0", mkstream=True)
    except Exception as exc:  # noqa: BLE001
        if "BUSYGROUP" not in str(exc):
            raise


def _redis_socket_timeout(config: AssetForgeConfig) -> float:
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


def _safe_key_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    return cleaned.strip("-")[:120] or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forge hosted digital goods from Etsy personalization events.")
    parser.add_argument("--once", action="store_true", help="Process one Redis consumer-group batch and exit.")
    parser.add_argument("--watch", action="store_true", help="Run forever.")
    parser.add_argument("--dry-run", action="store_true", help="Render local draft assets without calling the R2 bridge.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = AssetForgeConfig.from_env()
    redis_client = redis.from_url(config.redis_url(), decode_responses=True, socket_timeout=_redis_socket_timeout(config))
    uploader: AssetUploader = LocalDraftUploader(config.output_dir) if args.dry_run else HttpR2BridgeUploader(config.bridge_url, config.bridge_token)
    forge = AssetForge(redis_client=redis_client, uploader=uploader, config=config)
    if args.once or not args.watch:
        print(json.dumps(run_once(redis_client=redis_client, forge=forge, config=config, dry_run=args.dry_run), indent=2))
        return 0
    watch_loop(config, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
