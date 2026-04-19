"""BusPort — inter-agent messaging.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for the bus surface plugins and agents talk through.

Tenant binding: the bus resolves tenant from the caller's bus-token / agent
identity context. Methods here do NOT take explicit tenant_id — that matches
Inkwell's BusPort signature.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class BusMessage(BaseModel):
    """One message as it travels through the inter-agent bus.

    Mirrors Inkwell's BusMessage. The richer typed SOS bus envelopes live in
    sos.contracts.messages (AnnounceMessage, SendMessage, TaskCreatedMessage,
    ...). This is the port-level shape plugins and external agents see.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_: str = Field(alias="from", description="Sender agent slug")
    to: Optional[str] = Field(default=None, description="Recipient — None for broadcasts")
    text: str
    ts: str = Field(description="ISO-8601 timestamp")
    kind: Optional[str] = Field(default=None, description="Optional message-type hint")
    # `project` is optional in v0.9.0 and becomes required in v0.9.1 when
    # Phase 2 lands project-scope routing. Landing it here keeps the
    # generated TS schema stable across the bump.
    project: Optional[str] = Field(
        default=None,
        description="Project scope — required in v0.9.1 once bus scoping ships.",
    )


class SendRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    to: str = Field(description="Target agent slug or channel")
    text: str
    project: Optional[str] = Field(
        default=None,
        description="Project scope — required in v0.9.1.",
    )


class BroadcastRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    project: Optional[str] = Field(
        default=None,
        description="Project scope — required in v0.9.1.",
    )


class InboxRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: Optional[int] = Field(default=None, ge=1, le=1000)


# Subscriber callback signature — receives one BusMessage at a time.
BusSubscriber = Callable[[BusMessage], Awaitable[None]]

# The unsubscribe handle is a pure in-process concept: Inkwell typed it as
# `() => Promise<void>`, SOS models it as an awaitable callable. It never
# crosses a wire, so we keep it out of the schema export (no Pydantic
# model) and expose the type alias instead.
UnsubscribeHandle = Callable[[], Awaitable[None]]


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class BusPort(Protocol):
    """Inter-agent messaging port. Tenant bound via caller context."""

    async def send(self, req: SendRequest) -> None:
        """Deliver `req.text` to `req.to`."""
        ...

    async def broadcast(self, req: BroadcastRequest) -> None:
        """Publish `req.text` to all agents in the caller's tenant scope."""
        ...

    async def subscribe(self, callback: BusSubscriber) -> UnsubscribeHandle:
        """Register `callback` for every inbound message. Returns the
        awaitable the caller must await when tearing down."""
        ...

    async def inbox(self, req: InboxRequest) -> list[BusMessage]:
        """Recent messages addressed to the caller (or all for admins)."""
        ...


__all__ = [
    "BusMessage",
    "SendRequest",
    "BroadcastRequest",
    "InboxRequest",
    "UnsubscribeHandle",
    "BusSubscriber",
    "BusPort",
]
