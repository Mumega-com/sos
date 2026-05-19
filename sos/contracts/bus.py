"""
SOS Bus Contract - The Nervous System Interface ⚡

Defines the standard for inter-agent communication via Pub/Sub and Queues.
"""

from abc import ABC, abstractmethod
from typing import Callable, Any, Dict, Optional, List
from ..kernel.schema import Message

class BusContract(ABC):
    """
    Abstract interface for the Message Bus.
    Supports both Broadcast (Pub/Sub) and Direct (Queue) patterns.
    """

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the bus infrastructure."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Close connection."""
        pass

    @abstractmethod
    async def publish(self, channel: str, message: Message) -> bool:
        """
        Broadcast a message to a specific channel (Squad).
        Fire-and-forget.
        """
        pass

    @abstractmethod
    async def send(self, target_agent: str, message: Message) -> bool:
        """
        Send a direct message to an agent's inbox.
        Persistent/Guaranteed delivery (Queue).
        """
        pass

    @abstractmethod
    async def subscribe(self, channel: str, callback: Callable[[Message], Any]):
        """
        Listen to a channel.
        """
        pass

    @abstractmethod
    async def listen(self, agent_id: str, callback: Callable[[Message], Any]):
        """
        Listen to own direct inbox.
        """
        pass
