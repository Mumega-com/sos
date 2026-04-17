from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from sos.kernel import Capability


class CapabilityModel(BaseModel):
    id: str
    subject: str
    action: str
    resource: str
    constraints: Dict[str, Any] = Field(default_factory=dict)
    issued_at: str
    expires_at: str
    issuer: str
    signature: Optional[str] = None
    uses_remaining: Optional[int] = None
    parent_id: Optional[str] = None

    def to_capability(self) -> Capability:
        return Capability.from_dict(self.model_dump())

