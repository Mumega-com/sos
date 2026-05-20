"""Minimal public-safe adapter sketch for OpenClaw/Hermes-style hosts.

The adapter deliberately depends only on the public SOS SDK. A host runtime owns
its own process supervision, prompt queue, model calls, and token storage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sos.sdk import Agent, Message


MessageHandler = Callable[[Message], None]


@dataclass(frozen=True)
class HostProfile:
    agent: str
    runtime: str
    bridge_url: str
    project: str | None = None
    token_env: str | None = "SOS_BUS_TOKEN"
    token_file: Path | None = None
    subscriptions: tuple[str, ...] = field(default_factory=tuple)
    health_url: str | None = None

    def resolve_token(self) -> str:
        if self.token_file is not None:
            return self.token_file.expanduser().read_text(encoding="utf-8").strip()
        if self.token_env:
            return os.environ.get(self.token_env, "").strip()
        return ""


class HostRuntimeAdapter:
    """Bridge an external host runtime to SOS bus/profile primitives."""

    def __init__(self, profile: HostProfile, *, handler: MessageHandler | None = None) -> None:
        self.profile = profile
        self._handlers: list[MessageHandler] = []
        self.agent = Agent(
            name=profile.agent,
            token=profile.resolve_token(),
            project=profile.project,
            bridge_url=profile.bridge_url,
            subscriptions=profile.subscriptions,
        )
        if handler is not None:
            self._handlers.append(handler)
            self.agent.on_message(handler)

    def announce(self) -> None:
        self.agent.heartbeat(summary=f"{self.profile.agent} online via {self.profile.runtime}")

    def poll_once(self, *, limit: int = 10) -> list[Message]:
        messages = list(reversed(self.agent.inbox(limit=limit, update_cursor=True)))
        for message in messages:
            for handler in self._handlers:
                handler(message)
        return messages

    def send(self, to: str, text: str) -> None:
        self.agent.send(to, text)

    def health(self) -> dict[str, str]:
        return {
            "agent": self.profile.agent,
            "runtime": self.profile.runtime,
            "project": self.profile.project or "",
            "bridge_url": self.profile.bridge_url,
            "status": "configured" if self.profile.resolve_token() else "missing_token",
        }


def profile_from_env(runtime: str = "generic") -> HostProfile:
    return HostProfile(
        agent=os.environ.get("SOS_AGENT", runtime),
        runtime=runtime,
        project=os.environ.get("SOS_PROJECT") or None,
        bridge_url=os.environ.get("SOS_BRIDGE_URL", "http://localhost:6380"),
        token_env=os.environ.get("SOS_TOKEN_ENV", "SOS_BUS_TOKEN"),
        subscriptions=tuple(
            item.strip()
            for item in os.environ.get("SOS_SUBSCRIPTIONS", "").split(",")
            if item.strip()
        ),
        health_url=os.environ.get("SOS_HOST_HEALTH_URL") or None,
    )


__all__ = ["HostProfile", "HostRuntimeAdapter", "profile_from_env"]
