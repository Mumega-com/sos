"""
Sovereign Message Shim — replaces mumega.core.message dependencies.
Only the dataclasses needed by sovereign modules, no engine logic.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime


class MessageSource(Enum):
    CLI = "cli"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    API = "api"
    WEB = "web"
    SYSTEM = "system"


@dataclass
class Message:
    text: str
    user_id: str = "system"
    source: MessageSource = MessageSource.SYSTEM
    conversation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConversationContext:
    conversation_id: str = ""
    user_id: str = "system"
    source: MessageSource = MessageSource.SYSTEM
    metadata: Dict[str, Any] = field(default_factory=dict)
