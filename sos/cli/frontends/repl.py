"""
REPL Frontend - Simple Read-Eval-Print Loop for SOS chat.

Usage:
    mumega chat --frontend repl --agent river
"""

import asyncio
import json
import sys
from typing import Optional

import httpx

from sos.cli.frontends import ChatFrontend, ChatConfig, register_frontend


@register_frontend("repl")
class REPLFrontend(ChatFrontend):
    """
    Simple REPL (Read-Eval-Print Loop) frontend.

    Features:
    - Streaming responses (tokens appear as they arrive)
    - Conversation continuity (same conversation_id across turns)
    - Clean exit handling (Ctrl+C, 'exit', 'quit')
    """

    def __init__(self, config: ChatConfig):
        super().__init__(config)
        self._conversation_id = config.conversation_id or f"repl-{id(self)}"
        self._client: Optional[httpx.AsyncClient] = None

    async def run(self) -> None:
        """Start the REPL loop."""
        self._running = True

        # Show banner
        await self.display_status(
            f"Chatting with {self.config.agent}. Type 'exit' to quit."
        )
        print("-" * 40)

        async with httpx.AsyncClient(timeout=120.0) as client:
            self._client = client

            while self._running:
                try:
                    # Get user input
                    user_input = await self.get_input()

                    # Check for exit commands
                    if user_input.lower() in ("exit", "quit", "q", "/exit", "/quit"):
                        break

                    # Skip empty input
                    if not user_input.strip():
                        continue

                    # Send message and display response
                    await self._chat(user_input)

                except KeyboardInterrupt:
                    print()  # Newline after ^C
                    break
                except EOFError:
                    break

        print("Goodbye!")

    async def get_input(self) -> str:
        """Get user input (blocking, runs in thread)."""
        try:
            return await asyncio.to_thread(input, "You: ")
        except EOFError:
            return "exit"

    async def display_chunk(self, chunk: str) -> None:
        """Display a streaming chunk (no newline)."""
        print(chunk, end="", flush=True)

    async def display_response(self, response: str, metadata: dict = None) -> None:
        """Display a complete response."""
        print(response)
        if metadata and self.config.show_metadata:
            print(f"  [{metadata.get('model_used', 'unknown')}]")
        print()  # Blank line after response

    async def display_error(self, error: str) -> None:
        """Display an error message."""
        print(f"Error: {error}", file=sys.stderr)

    async def display_status(self, status: str) -> None:
        """Display a status message."""
        print(status)

    async def _chat(self, message: str) -> None:
        """Send a message and display the response."""
        if not self._client:
            await self.display_error("Client not initialized")
            return

        # Print agent name prefix
        print(f"{self.config.agent.title()}: ", end="", flush=True)

        if self.config.streaming:
            await self._chat_stream(message)
        else:
            await self._chat_sync(message)

    async def _chat_stream(self, message: str) -> None:
        """Send message with streaming response."""
        url = f"{self.config.engine_url}/chat"
        payload = {
            "message": message,
            "agent_id": f"agent:{self.config.agent}",
            "conversation_id": self._conversation_id,
            "stream": True,
            "memory_enabled": True,
        }

        try:
            async with self._client.stream("POST", url, json=payload) as response:
                if response.status_code != 200:
                    await self.display_error(f"HTTP {response.status_code}")
                    return

                metadata = {}
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)

                            if "chunk" in data:
                                await self.display_chunk(data["chunk"])
                            elif "done" in data:
                                metadata = data
                            elif "error" in data:
                                print()  # Newline before error
                                await self.display_error(data["error"])
                                return

                        except json.JSONDecodeError:
                            continue

                # Newline after streaming complete
                print()
                if metadata and self.config.show_metadata:
                    print(f"  [{metadata.get('model_used', 'unknown')}]")
                print()  # Blank line

        except httpx.ConnectError:
            print()  # Newline
            await self.display_error(
                f"Cannot connect to engine at {self.config.engine_url}\n"
                "  Run: mumega start engine"
            )
        except Exception as e:
            print()  # Newline
            await self.display_error(str(e))

    async def _chat_sync(self, message: str) -> None:
        """Send message with synchronous response."""
        url = f"{self.config.engine_url}/chat"
        payload = {
            "message": message,
            "agent_id": f"agent:{self.config.agent}",
            "conversation_id": self._conversation_id,
            "stream": False,
            "memory_enabled": True,
        }

        try:
            response = await self._client.post(url, json=payload)

            if response.status_code != 200:
                await self.display_error(f"HTTP {response.status_code}")
                return

            data = response.json()
            await self.display_response(
                data.get("content", ""),
                metadata={"model_used": data.get("model_used")}
            )

        except httpx.ConnectError:
            await self.display_error(
                f"Cannot connect to engine at {self.config.engine_url}\n"
                "  Run: mumega start engine"
            )
        except Exception as e:
            await self.display_error(str(e))
