"""CRMPort — contact and message surface for tenant CRMs.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how plugins create leads, update contacts, and send
outbound messages without knowing the underlying CRM.

Tenant binding: EXPLICIT tenant_id on every method. A CRM adapter is
usually bound to one tenant at construction, but the platform layer
needs to fan out — and the port contract must say so.

Divergence from Inkwell
-----------------------
Inkwell's CRMPort v6 exposed only createContact / updateContact /
createOpportunity. The SOS spec for this task asks for a fuller surface
(get / list / send_message) because SOS is the place where multi-channel
messaging lands. We keep createContact/updateContact identical-shaped and
add the three SOS-side methods. When Inkwell widens, we'll fold them in.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class ContactData(BaseModel):
    """Write shape for create/update. `extra` carries arbitrary fields
    the underlying CRM supports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    extra: Optional[dict[str, Any]] = None


class Contact(BaseModel):
    """Read shape — what the CRM returns. Kept intentionally minimal so
    adapters can extend in `extra`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tenant_id: str
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    extra: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CreateContactRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    contact: ContactData


class CreateContactResult(BaseModel):
    """Mirrors Inkwell's `createContact → string` — the new CRM contact ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contact_id: str


class GetContactRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    contact_id: str


class UpdateContactRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    contact_id: str
    patch: ContactData


class ListContactsFilters(BaseModel):
    """Optional filters — backend-dependent semantics. Most adapters honor
    email / name substring matches and arbitrary `extra` key lookups."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: Optional[str] = None
    name: Optional[str] = None
    extra: Optional[dict[str, Any]] = None
    limit: Optional[int] = Field(default=None, ge=1, le=1000)
    cursor: Optional[str] = None


class ListContactsRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    filters: Optional[ListContactsFilters] = None


class MessageContent(BaseModel):
    """Outbound message payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: str = Field(
        description="'email' | 'sms' | 'whatsapp' | 'telegram' | adapter-specific."
    )
    subject: Optional[str] = None
    body: str


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    contact_id: str
    message: MessageContent


class SendMessageResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    delivered: bool
    reason: Optional[str] = None


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class CRMPort(Protocol):
    """CRM surface — contacts and outbound messaging."""

    async def create_contact(self, req: CreateContactRequest) -> CreateContactResult:
        """Create a new contact. Returns the CRM's contact ID."""
        ...

    async def get_contact(self, req: GetContactRequest) -> Optional[Contact]:
        """Fetch a contact by ID — None if not found."""
        ...

    async def update_contact(self, req: UpdateContactRequest) -> None:
        """Apply a partial contact update."""
        ...

    async def list_contacts(self, req: ListContactsRequest) -> list[Contact]:
        """List contacts matching optional filters."""
        ...

    async def send_message(self, req: SendMessageRequest) -> SendMessageResult:
        """Dispatch an outbound message to a contact."""
        ...


__all__ = [
    "ContactData",
    "Contact",
    "CreateContactRequest",
    "CreateContactResult",
    "GetContactRequest",
    "UpdateContactRequest",
    "ListContactsFilters",
    "ListContactsRequest",
    "MessageContent",
    "SendMessageRequest",
    "SendMessageResult",
    "CRMPort",
]
