"""
SOS Discord Adapter - The New Gateway to the Swarm.

Responsibilities:
1. Connects the SOS Swarm Council to Discord channels.
2. Witness Bridge: Direct human-in-the-loop voting for Council Proposals.
3. Automated Alerts: Health and Content signals routed to specific channels.
"""

import os
import asyncio
import logging
import json
from typing import Optional, List, Dict, Any
from pathlib import Path

import discord
from discord.ext import commands
from discord import ui

from sos.clients.engine import AsyncEngineClient
from sos.observability.logging import get_logger
from sos.services.bus.core import get_bus
from sos.kernel import Message, MessageType

log = get_logger("adapter_discord")

# Configuration
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
WEB_APP_URL = os.environ.get("SOS_WEB_APP_URL", "https://tma.mumega.io")
WEBHOOKS_PATH = Path.home() / ".mumega" / "discord_webhooks.json"

# Load Channel Mappings
with open(WEBHOOKS_PATH) as f:
    CHANNEL_MAP = json.load(f).get("channels", {})

class WitnessView(ui.View):
    """Interactive buttons for Proposal Approval/Rejection."""
    def __init__(self, proposal_id: str, squad: str):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id
        self.squad = squad

    @ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="approve")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        await self._submit_vote(interaction, "PASS")

    @ui.button(label="❌ Reject", style=discord.ButtonStyle.danger, custom_id="reject")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        await self._submit_vote(interaction, "FAIL")

    async def _submit_vote(self, interaction: discord.Interaction, vote: str):
        bus = get_bus()
        await bus.connect()
        
        vote_msg = Message(
            source=f"user:discord:{interaction.user.id}",
            type=MessageType.SIGNAL,
            payload={
                "event": "PROPOSAL_VOTE",
                "proposal_id": self.proposal_id,
                "vote": vote,
                "voter": "human_architect"
            }
        )
        await bus.send(vote_msg, target_squad=self.squad)
        
        status_emoji = "✅ Approved" if vote == "PASS" else "❌ Rejected"
        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n\n---\n**Human Witness Result:** {status_emoji}",
            view=None
        )

class SwarmBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.engine_client = AsyncEngineClient(base_url="http://localhost:6060")

    async def setup_hook(self):
        self.loop.create_task(self.start_witness_bridge())

    async def start_witness_bridge(self):
        """Listens for Council Proposals on Redis and forwards to Discord."""
        bus = get_bus()
        await bus.connect()
        
        log.info("👁️ Discord Witness Bridge connected to Redis bus.")
        
        async for msg in bus.subscribe("broadcast", squads=["core", "marketing"]):
            try:
                payload = msg.payload
                event = payload.get("event")
                
                # 1. Content Witnessing -> mission-control
                if event == "CONTENT_WITNESS":
                    channel_id = int(CHANNEL_MAP.get("mission-control", 0))
                    if channel_id:
                        channel = self.get_channel(channel_id)
                        if channel:
                            text = (
                                f"📝 **Content Proposal: {payload.get('title')}**\n\n"
                                f"{payload.get('content_preview')}...\n\n"
                                f"_Agent: {msg.source}_"
                            )
                            view = WitnessView(payload.get("proposal_id"), "marketing")
                            await channel.send(text, view=view)
                
                # 2. Heal Requests -> alerts
                elif event == "HEAL_REQUEST":
                    channel_id = int(CHANNEL_MAP.get("alerts", 0))
                    if channel_id:
                        channel = self.get_channel(channel_id)
                        if channel:
                            text = (
                                f"🚨 **Heal Request: {payload.get('organ')}**\n\n"
                                f"Diagnostic: {payload.get('diagnostic')}\n"
                                f"Instruction: {payload.get('instruction')}\n\n"
                                f"_Priority: {payload.get('priority')}_"
                            )
                            view = WitnessView(payload.get("proposal_id"), "core")
                            await channel.send(text, view=view)
                            
            except Exception as e:
                log.error(f"Discord Witness Bridge error: {e}")

bot = SwarmBot()

@bot.command()
async def status(ctx):
    """Check Swarm Status."""
    try:
        health = await bot.engine_client.health()
        await ctx.send(f"✅ **Sovereign Engine:** `{health['status']}` (Port: 6060)")
    except Exception as e:
        await ctx.send(f"⚠️ **Engine Offline**: {e}")

async def start_discord_adapter():
    """Main entry point for starting the Discord bot."""
    if not TOKEN:
        log.error("DISCORD_BOT_TOKEN missing. Cannot start adapter.")
        return

    log.info("🚀 Starting SOS Discord Adapter...")
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(start_discord_adapter())
