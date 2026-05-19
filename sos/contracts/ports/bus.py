"""BusPort — inter-agent messaging.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for the bus surface plugins and agents talk through.

Scope model (v0.9.1):

* ``tenant_id`` is the **hard** customer boundary. Two tenants never see
  each other's messages — enforcement lives at the delivery layer and
  this field is the anchor. Resolved from the caller's bus-token /
  agent identity context at publish time; wire shape carries it so
  every message can be audited independently of the session that
  produced it.
* ``project`` is a **soft** grouping inside a tenant: free-form string,
  used to fan routing (``journeys``, ``saas``, ``mothership``...). No
  implicit default — callers must name the project the message belongs
  to, so scope leaks show up as contract errors instead of silent
  cross-project reads.

Methods on :class:`BusPort` still don't take explicit ``tenant_id``
(matches Inkwell's signature) — the port resolves it from caller
context and stamps it on every outgoing envelope.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class BusMessage(BaseModel):
    """One message as it travels through the inter-agent bus.

    Mirrors Inkwell's BusMessage. The richer typed SOS bus envelopes live in
    sos.contracts.messages (AnnounceMessage, SendMessage, TaskCreatedMessage,
    ...). This is the port-level shape plugins and external agents see.

    Both ``tenant_id`` and ``project`` are required in v0.9.1. See module
    docstring for the hard-vs-soft distinction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_: str = Field(alias="from", description="Sender agent slug")
    to: Optional[str] = Field(default=None, description="Recipient — None for broadcasts")
    text: str
    ts: str = Field(description="ISO-8601 timestamp")
    kind: Optional[str] = Field(default=None, description="Optional message-type hint")
    tenant_id: str = Field(
        description="Hard customer boundary. Stamped by the port from caller context."
    )
    project: str = Field(
        description="Soft grouping inside a tenant. Required — no implicit default."
    )


class SendRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    to: str = Field(description="Target agent slug or channel")
    text: str
    project: str = Field(description="Soft grouping inside a tenant. Required.")


class BroadcastRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    project: str = Field(description="Soft grouping inside a tenant. Required.")


class InboxRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: Optional[int] = Field(default=None, ge=1, le=1000)


AckStatus = Literal["ok", "nack", "dlq"]


class BusAck(BaseModel):
    """Result of acknowledging a consumed bus message.

    Ack is the verb the consumer uses to tell the bus "I've finished
    with this message, release it from the pending-entries list". We
    surface three status values because the semantics diverge:

    * ``ok`` — normal completion. XACK removes the entry from PEL.
    * ``nack`` — soft failure. XACK still fires (otherwise the message
      stays pending forever and blocks retry), but the consumer signals
      that the message should be re-queued for another attempt. Retry
      policy is the delivery layer's business (see W3); this field only
      records intent.
    * ``dlq`` — terminal failure. Message has exceeded retry budget and
      is being routed to the dead-letter stream for audit. Consumers
      rarely emit this directly — the retry worker does (see W4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str = Field(description="Stream entry ID returned by XADD (e.g. '1712345678-0').")
    acked_at: str = Field(description="ISO-8601 timestamp when the ack was issued.")
    status: AckStatus = Field(description="Disposition: ok | nack | dlq.")


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

    async def ack(self, message_id: str, status: AckStatus = "ok") -> BusAck:
        """Acknowledge a consumed message by its stream ID.

        The adapter is already scoped to a tenant/project at construction
        time (tenant-ambient port) and knows which consumer group its
        subscribe loop joined, so only ``message_id`` + disposition
        cross the port. Implementations MUST XACK the underlying stream
        for ``ok`` and ``dlq``; ``nack`` also XACKs but signals the
        delivery-layer retry worker to re-enqueue.
        """
        ...


__all__ = [
    "BusMessage",
    "SendRequest",
    "BroadcastRequest",
    "InboxRequest",
    "BusAck",
    "AckStatus",
    "UnsubscribeHandle",
    "BusSubscriber",
    "BusPort",
]
