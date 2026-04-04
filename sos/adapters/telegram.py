"""
SOS Telegram Adapter - The Gateway to the Swarm.

Responsibilities:
1. Authenticates users via allowed user list.
2. Launches the Sovereign Mini App (The Deck).
3. Routes chat messages to the SOS Engine.
4. Witness Bridge: Direct human-in-the-loop voting for Council Proposals.
"""

import os
import asyncio
import logging
import json
from typing import Optional, List, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from sos.clients.engine import AsyncEngineClient
from sos.contracts.engine import ChatRequest
from sos.observability.logging import get_logger
from sos.services.bus.core import get_bus
from sos.kernel import Message, MessageType

log = get_logger("adapter_telegram")

# Configuration
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USERS = os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
WEB_APP_URL = os.environ.get("SOS_WEB_APP_URL", "https://tma.mumega.io")
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "765204057") # Default to you based on logs

bot = Bot(token=TOKEN) if TOKEN else None
dp = Dispatcher()
engine_client = AsyncEngineClient(base_url="http://localhost:6060")

# --- WITNESS BRIDGE: Redis Subscription ---

async def start_witness_bridge():
    """Listens for Council Proposals on Redis and forwards to Telegram."""
    bus = get_bus()
    await bus.connect()
    
    log.info("👁️ Telegram Witness Bridge connected to Redis bus.")
    
    # Subscribe to broadcast events via the bus service
    # The bus handles the underlying channel names (sos:channel:global etc)
    async for msg in bus.subscribe("broadcast", squads=["core", "marketing"]):
        try:
            payload = msg.payload
            event = payload.get("event")
            
            log.info(f"Broadcast signal received: {event}")
            
            # 1. Content Witnessing
            if event == "CONTENT_WITNESS":
                await send_proposal_to_admin(
                    f"📝 **Content Proposal: {payload.get('title')}**\n\n"
                    f"{payload.get('content_preview')}...\n\n"
                    f"_Agent: {msg.source}_",
                    proposal_id=payload.get("proposal_id"),
                    squad="marketing"
                )
            
            # 2. Heal Requests (Immune System)
            elif event == "HEAL_REQUEST":
                await send_proposal_to_admin(
                    f"🚨 **Heal Request: {payload.get('organ')}**\n\n"
                    f"Diagnostic: {payload.get('diagnostic')}\n"
                    f"Instruction: {payload.get('instruction')}\n\n"
                    f"_Priority: {payload.get('priority')}_",
                    proposal_id=payload.get("proposal_id"),
                    squad="core"
                )
                
        except Exception as e:
            log.error(f"Witness Bridge error: {e}")

async def send_proposal_to_admin(text: str, proposal_id: str, squad: str):
    """Sends a proposal message with Approve/Reject buttons."""
    if not ADMIN_CHAT_ID or not bot:
        log.warning("Cannot send proposal: ADMIN_CHAT_ID or bot missing.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"vote:{proposal_id}:pass:{squad}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"vote:{proposal_id}:fail:{squad}")
        ],
        [InlineKeyboardButton(text="👁️ View in Deck", web_app=WebAppInfo(url=f"{WEB_APP_URL}/proposals/{proposal_id}"))]
    ])

    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=text,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# --- CALLBACK HANDLERS: Voting ---

@dp.callback_query(F.data.startswith("vote:"))
async def handle_vote(callback: CallbackQuery):
    """Captures human vote and pushes to Redis bus."""
    parts = callback.data.split(":")
    if len(parts) < 4: return
    
    proposal_id, result, squad = parts[1], parts[2], parts[3]
    passed = (result == "pass")
    
    bus = get_bus()
    await bus.connect()
    
    vote_msg = Message(
        source=f"user:{callback.from_user.id}",
        type=MessageType.SIGNAL,
        payload={
            "event": "PROPOSAL_VOTE",
            "proposal_id": proposal_id,
            "vote": "PASS" if passed else "FAIL",
            "voter": "human_architect"
        }
    )
    
    await bus.send(vote_msg, target_squad=squad)
    
    status_emoji = "✅ Approved" if passed else "❌ Rejected"
    await callback.message.edit_text(
        text=f"{callback.message.text}\n\n---\n**Human Witness Result:** {status_emoji}",
        parse_mode="Markdown"
    )
    await callback.answer(f"Witness result recorded: {result}")

# --- COMMAND HANDLERS (Standard) ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = str(message.from_user.id)
    # Check if user is allowed
    is_allowed = not ALLOWED_USERS or user_id in ALLOWED_USERS
    if not is_allowed:
        await message.answer("🚫 Sovereign access denied.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="OPEN THE DECK ⚡", web_app=WebAppInfo(url=WEB_APP_URL))]
    ])

    await message.answer(
        f"🌿 **Sovereign OS [ACTIVE]**\n\n"
        f"Welcome node `agent:{user_id}`.\n"
        f"Witness Bridge is active. You will receive critical proposals here.",
        reply_markup=markup,
        parse_mode="Markdown"
    )

async def start_telegram_adapter():
    """Main entry point for starting the Telegram bot."""
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN missing. Cannot start adapter.")
        return

    log.info("🚀 Starting SOS Telegram Adapter...")
    
    # Start Witness Bridge background task
    asyncio.create_task(start_witness_bridge())
    
    # Start polling
    await dp.start_polling(bot)
