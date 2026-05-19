"""Public SOS SDK for external agents.

This module hides Redis stream details while preserving the recovery fields
watchers need: stream_id cursor, sender, text, request_id, and stream
provenance.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sos.bus import envelope


MessageHandler = Callable[["Message"], Any]
DEFAULT_BRIDGE_URL = "https://bus.mumega.com"


@dataclass(frozen=True)
class StreamRef:
    """A concrete Redis stream watched by an agent inbox."""

    name: str
    kind: str


@dataclass(frozen=True)
class Message:
    """Structured inbox message returned by :class:`Agent`."""

    stream_id: str
    stream: str
    stream_kind: str
    sender: str
    target: str
    text: str
    timestamp: float | None = None
    message_id: str | None = None
    request_id: str | None = None
    project: str | None = None
    msg_type: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def source(self) -> str:
        """Compatibility alias for callers using bus envelope language."""
        return self.sender


@dataclass(frozen=True)
class SendResult:
    """Result of a send operation."""

    stream_id: str
    message_id: str
    stream: str
    channel: str
    wake_channel: str
    target: str
    text: str


@dataclass(frozen=True)
class HeartbeatResult:
    """Result of a heartbeat emission."""

    registry_key: str
    stream_id: str | None


class Agent:
    """Minimal Redis-backed SDK.

    Example:

        from sos.sdk import Agent

        agent = Agent(token="sk-bus-...", name="hadi-codex", project="sos")
        agent.on_message(lambda message: print(message.text))
        agent.start()
    """

    def __init__(
        self,
        *,
        token: str = "",
        name: str,
        project: str | None = None,
        redis_url: str | None = None,
        redis_client: Any | None = None,
        bridge_url: str | None = None,
        state_dir: str | Path | None = None,
        include_sos_project: bool = True,
        heartbeat_interval: float = 300.0,
        subscriptions: Iterable[str] | None = None,
    ) -> None:
        self.token = token
        self.name = _normalize_agent_name(name)
        self.project = project or os.environ.get("SOS_PROJECT") or None
        self.redis_url = redis_url or _default_redis_url()
        self._redis = redis_client
        default_bridge_url = DEFAULT_BRIDGE_URL if redis_client is None and redis_url is None else ""
        self.bridge_url = (
            bridge_url
            if bridge_url is not None
            else os.environ.get("SOS_BRIDGE_URL")
            or os.environ.get("MUMEGA_BRIDGE_URL")
            or default_bridge_url
        ).rstrip("/")
        self.include_sos_project = include_sos_project
        self.state_dir = Path(state_dir) if state_dir else Path.home() / ".sos" / "cursors"
        self.cursor_path = (
            self.state_dir / "cursor.json"
            if state_dir
            else self.state_dir / f"{self.name}.json"
        )
        self.heartbeat_interval = heartbeat_interval
        self._last_heartbeat_at = 0.0
        self._handlers: list[MessageHandler] = []
        default_subscriptions = (
            [f"sos:channel:project:{self.project}:global"]
            if subscriptions is None and self.project
            else []
        )
        self.subscriptions = tuple(
            _normalize_subscriptions(
                subscriptions if subscriptions is not None else default_subscriptions
            )
        )

    def on_message(self, handler: MessageHandler | None = None):
        """Register a message handler.

        Supports both ``agent.on_message(fn)`` and decorator style.
        """
        if handler is None:
            def decorator(fn: MessageHandler) -> MessageHandler:
                self._handlers.append(fn)
                return fn

            return decorator
        self._handlers.append(handler)
        return handler

    def streams(self) -> list[StreamRef]:
        """Return all inbox streams watched by this agent."""
        streams: list[StreamRef] = []
        seen: set[str] = set()

        def add(name: str, kind: str) -> None:
            if name not in seen:
                seen.add(name)
                streams.append(StreamRef(name=name, kind=kind))

        if self.project:
            add(f"sos:stream:project:{self.project}:agent:{self.name}", "project")
        elif self.include_sos_project:
            add(f"sos:stream:project:sos:agent:{self.name}", "project-sos")

        add(f"sos:stream:global:agent:{self.name}", "global")
        add(f"sos:stream:agent:{self.name}", "legacy-global")
        add(f"sos:stream:sos:channel:private:agent:{self.name}", "legacy-private")
        for subscription in self.subscriptions:
            stream = _subscription_stream(subscription, self.project)
            if stream:
                add(stream, f"subscription:{subscription}")
        return streams

    def inbox(
        self,
        *,
        limit: int = 10,
        since: str | None = None,
        include_self: bool = False,
        update_cursor: bool = False,
    ) -> list[Message]:
        """Read structured messages from all inbox streams.

        ``since`` is an exclusive Redis stream ID cursor. If omitted, the saved
        cursor is used when present. Results are newest-first.
        """
        cursor = since if since is not None else self.load_cursor()
        messages = self._read_inbox(limit=limit, since=cursor)
        if not include_self:
            messages = [m for m in messages if _agent_slug(m.sender) != self.name]
        messages.sort(key=lambda msg: _stream_id_sort_key(msg.stream_id), reverse=True)
        messages = _dedup_messages(messages)
        messages = messages[:limit]
        if update_cursor and messages:
            self.save_cursor(max((m.stream_id for m in messages), key=_stream_id_sort_key))
        return messages

    def send(
        self,
        to: str,
        text: str,
        *,
        project: str | None = None,
        msg_type: str = "chat",
        extras: dict[str, Any] | None = None,
    ) -> SendResult:
        """Send a message and return both Redis stream id and envelope id."""
        if self.bridge_url and self._redis is None:
            return self._send_via_bridge(to, text, project=project)
        target = _normalize_agent_name(to)
        project_scope = project if project is not None else self.project
        stream = _agent_stream(target, project_scope)
        channel = _agent_channel(target, project_scope)
        wake_channel = f"sos:wake:{target}"
        payload = envelope.build(
            msg_type=msg_type,
            source=f"agent:{self.name}",
            target=f"agent:{target}",
            text=text,
            project=project_scope,
            extras=extras,
        )
        stream_id = self.redis.xadd(stream, payload)
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode()

        self.redis.publish(channel, json.dumps(payload, ensure_ascii=False))
        self.redis.publish(
            wake_channel,
            json.dumps(
                {"source": f"agent:{self.name}", "from": self.name, "text": text},
                ensure_ascii=False,
            ),
        )
        return SendResult(
            stream_id=str(stream_id),
            message_id=payload["id"],
            stream=stream,
            channel=channel,
            wake_channel=wake_channel,
            target=target,
            text=text,
        )

    def onboard(self, *, summary: str = "") -> SendResult:
        """Announce agent presence on the bus.

        External agents (HTTP-only, no direct Redis) call this once at startup
        to appear in /peers and let other agents know they are online.
        """
        text = summary or f"{self.name} online"
        return self.send(
            "broadcast",
            text,
            project=self.project,
            msg_type="heartbeat",
        )

    def reply(self, message: Message, text: str, **kwargs: Any) -> SendResult:
        """Reply to a message sender."""
        return self.send(_agent_slug(message.sender), text, **kwargs)

    def heartbeat(self, *, summary: str = "", tool: str = "sdk") -> HeartbeatResult:
        """Emit a liveness heartbeat to registry and broadcast stream."""
        now = datetime.now(timezone.utc).isoformat()
        registry_key = f"sos:registry:{self.name}"
        mapping = {
            "agent": self.name,
            "tool": tool,
            "summary": summary or f"{self.name} via SOS SDK",
            "last_seen": now,
        }
        if self.project:
            mapping["project"] = self.project
        self.redis.hset(registry_key, mapping=mapping)

        stream_id = None
        try:
            fields = envelope.build(
                msg_type="heartbeat",
                source=f"agent:{self.name}",
                target="agent:broadcast",
                text=mapping["summary"],
                project=self.project,
                extras={"tool": tool, "last_seen": now},
            )
            stream_id = self.redis.xadd("sos:stream:global:agent:broadcast", fields)
            if isinstance(stream_id, bytes):
                stream_id = stream_id.decode()
        except Exception:
            stream_id = None
        self._last_heartbeat_at = time.monotonic()
        return HeartbeatResult(registry_key=registry_key, stream_id=str(stream_id) if stream_id else None)

    def start(
        self,
        *,
        poll_interval: float = 3.0,
        limit: int = 10,
        once: bool = False,
    ) -> None:
        """Run polling loop with heartbeat and exponential backoff."""
        backoff = poll_interval
        while True:
            try:
                self._heartbeat_if_due()
                messages = list(reversed(self.inbox(limit=limit, update_cursor=True)))
                for message in messages:
                    for handler in self._handlers:
                        handler(message)
                backoff = poll_interval
                if once:
                    return
                time.sleep(poll_interval)
            except Exception:
                if once:
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                self._redis = None

    @property
    def redis(self) -> Any:
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    def load_cursor(self) -> str | None:
        try:
            data = json.loads(self.cursor_path.read_text())
        except Exception:
            return None
        cursor = data.get("last_stream_id")
        return str(cursor) if cursor else None

    def save_cursor(self, stream_id: str) -> None:
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        self.cursor_path.write_text(json.dumps({"last_stream_id": stream_id}, indent=2) + "\n")

    def _heartbeat_if_due(self) -> None:
        if time.monotonic() - self._last_heartbeat_at >= self.heartbeat_interval:
            self.heartbeat()

    def _read_inbox(self, *, limit: int, since: str | None) -> list[Message]:
        if self.bridge_url:
            return self._read_inbox_via_bridge(limit=limit, since=since)
        messages: list[Message] = []
        min_id = f"({since}" if since and _valid_stream_id(since) else "-"
        for stream_ref in self.streams():
            try:
                rows = self.redis.xrange(stream_ref.name, min=min_id, max="+", count=limit)
            except TypeError:
                rows = self.redis.xrange(stream_ref.name, min_id, "+", limit)
            messages.extend(_messages_from_rows(rows, stream_ref))
        return messages

    def _send_via_bridge(
        self,
        to: str,
        text: str,
        *,
        project: str | None = None,
    ) -> SendResult:
        target = _normalize_agent_name(to)
        project_scope = project if project is not None else self.project
        body = json.dumps({
            "from": self.name,
            "to": target,
            "text": text,
            **({"project": project_scope} if project_scope else {}),
        }).encode("utf-8")
        url = f"{self.bridge_url}/send"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        stream = payload.get("stream") or _agent_stream(target, project_scope)
        return SendResult(
            stream_id=str(payload.get("entry_id") or ""),
            message_id=str(payload.get("message_id") or ""),
            stream=stream,
            channel=_agent_channel(target, project_scope),
            wake_channel=f"sos:wake:{target}",
            target=target,
            text=text,
        )

    def _read_inbox_via_bridge(self, *, limit: int, since: str | None) -> list[Message]:
        query = {
            "agent": self.name,
            "limit": str(limit),
            "format": "json",
        }
        if self.project:
            query["project"] = self.project
        if since:
            query["since"] = since
        if self.subscriptions:
            query["subscriptions"] = list(self.subscriptions)
        url = f"{self.bridge_url}/inbox?{urllib.parse.urlencode(query, doseq=True)}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [_message_from_bridge(item) for item in payload.get("messages", [])]


def _messages_from_rows(rows: Iterable[Any], stream_ref: StreamRef) -> list[Message]:
    messages: list[Message] = []
    for stream_id, fields in rows:
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode()
        if not isinstance(fields, dict):
            continue
        decoded = {_decode(k): _decode(v) for k, v in fields.items()}
        parsed = envelope.parse(decoded)
        extras = parsed.get("extras") or {}
        request_id = (
            extras.get("request_id")
            or extras.get("requestId")
            or decoded.get("request_id")
            or decoded.get("requestId")
        )
        messages.append(
            Message(
                stream_id=str(stream_id),
                stream=stream_ref.name,
                stream_kind=stream_ref.kind,
                sender=str(parsed.get("source") or decoded.get("source") or ""),
                target=str(parsed.get("target") or decoded.get("target") or ""),
                text=str(parsed.get("text") or decoded.get("text") or ""),
                timestamp=parsed.get("timestamp"),
                message_id=parsed.get("id"),
                request_id=str(request_id) if request_id else None,
                project=parsed.get("project") or decoded.get("project"),
                msg_type=str(parsed.get("type") or decoded.get("type") or ""),
                raw=decoded,
            )
        )
    return messages


def _message_from_bridge(raw: dict[str, Any]) -> Message:
    return Message(
        stream_id=str(raw.get("stream_id") or raw.get("id") or ""),
        stream=str(raw.get("stream") or ""),
        stream_kind=str(raw.get("stream_kind") or "bridge"),
        sender=str(raw.get("sender") or raw.get("source") or ""),
        target=str(raw.get("target") or ""),
        text=str(raw.get("text") or ""),
        timestamp=raw.get("timestamp"),
        message_id=str(raw.get("message_id") or raw.get("id") or "") or None,
        request_id=str(raw.get("request_id") or "") or None,
        project=raw.get("project"),
        msg_type=str(raw.get("type") or raw.get("msg_type") or ""),
        raw=raw,
    )


def _dedup_messages(messages: list[Message]) -> list[Message]:
    seen: set[tuple[str | None, str, str]] = set()
    deduped: list[Message] = []
    for message in messages:
        key = (message.message_id, message.sender, message.text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(message)
    return deduped


def _default_redis_url() -> str:
    if os.environ.get("REDIS_URL"):
        return os.environ["REDIS_URL"]
    password = os.environ.get("REDIS_PASSWORD")
    if password:
        return f"redis://:{password}@localhost:6379/0"
    return "redis://localhost:6379/0"


def _agent_stream(agent: str, project: str | None) -> str:
    if project:
        return f"sos:stream:project:{project}:agent:{agent}"
    return f"sos:stream:global:agent:{agent}"


def _agent_channel(agent: str, project: str | None) -> str:
    if project:
        return f"sos:channel:project:{project}:agent:{agent}"
    return f"sos:channel:agent:{agent}"


def _normalize_subscriptions(subscriptions: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in subscriptions:
        value = str(raw).strip()
        if not value:
            continue
        if not value.startswith("sos:channel:"):
            value = f"sos:channel:{value}"
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def _subscription_stream(subscription: str, project: str | None) -> str | None:
    if subscription in {"sos:channel:global", "sos:channel:broadcast"}:
        if project:
            return f"sos:stream:project:{project}:broadcast"
        return "sos:stream:global:broadcast"
    if subscription.startswith("sos:channel:squad:"):
        squad = subscription.removeprefix("sos:channel:squad:")
        return f"sos:stream:global:squad:{squad}" if squad else None
    if subscription.startswith("sos:channel:project:"):
        parts = subscription.split(":")
        if len(parts) < 5:
            return None
        channel_project = parts[3]
        if project and channel_project != project:
            return None
        channel_kind = parts[4]
        if channel_kind in {"global", "broadcast"}:
            return f"sos:stream:project:{channel_project}:broadcast"
        if channel_kind == "squad" and len(parts) >= 6 and parts[5]:
            return f"sos:stream:project:{channel_project}:squad:{parts[5]}"
    return None


def _normalize_agent_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return normalized.strip("-")


def _agent_slug(source: str) -> str:
    return source.removeprefix("agent:")


def _valid_stream_id(value: str) -> bool:
    return bool(re.fullmatch(r"\d+-\d+", value))


def _stream_id_sort_key(value: str) -> tuple[int, int]:
    try:
        left, right = value.split("-", 1)
        return int(left), int(right)
    except Exception:
        return 0, 0


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


__all__ = ["Agent", "HeartbeatResult", "Message", "SendResult", "StreamRef"]
