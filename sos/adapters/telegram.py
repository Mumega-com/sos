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


# --- WIRE 7: Bounty Payout Approval ---

async def send_payout_approval(bounty_id: str, task_title: str, agent: str, amount: float):
    """Wire 7: Send bounty payout approval request to Hadi via Telegram.

    Called when treasury.pay_bounty_with_witness() returns pending_approval
    for payouts >= 100 MIND.
    """
    if not ADMIN_CHAT_ID or not bot:
        log.warning("Cannot send payout approval: ADMIN_CHAT_ID or bot missing.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Approve {amount:.0f} MIND",
                callback_data=f"payout:{bounty_id}:approve",
            ),
            InlineKeyboardButton(
                text="Reject",
                callback_data=f"payout:{bounty_id}:reject",
            ),
        ]
    ])

    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(
            f"**Bounty Payout Approval**\n\n"
            f"Task: {task_title}\n"
            f"Agent: {agent}\n"
            f"Amount: **{amount:.0f} MIND**\n"
            f"Bounty: `{bounty_id}`\n\n"
            f"Tap to approve or reject."
        ),
        reply_markup=markup,
        parse_mode="Markdown",
    )
    log.info(f"Wire 7: Sent payout approval to Telegram — {bounty_id} ({amount} MIND)")


@dp.callback_query(F.data.startswith("payout:"))
async def handle_payout_approval(callback: CallbackQuery):
    """Wire 7: Handle payout approve/reject from Telegram.

    Flow: Telegram tap → governance.approve() → treasury.approve_payout() → Solana transfer.
    """
    parts = callback.data.split(":")
    if len(parts) < 3:
        return

    bounty_id, action = parts[1], parts[2]
    approved = (action == "approve")

    if approved:
        try:
            # 1. Governance approval
            from sos.kernel.governance import approve as gov_approve
            await gov_approve(
                intent_id=f"payout:{bounty_id}",
                approver="hadi",
                tenant="mumega",
            )

            # 2. Treasury payout execution
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path.home()))
            from sovereign.treasury import TreasuryWallet

            treasury = TreasuryWallet()
            tx_sig = await treasury.approve_payout(bounty_id, witness_id="hadi")

            await callback.message.edit_text(
                text=(
                    f"{callback.message.text}\n\n"
                    f"---\n**Approved** by Hadi\n"
                    f"Tx: `{tx_sig}`"
                ),
                parse_mode="Markdown",
            )
            await callback.answer(f"Payout approved. Tx: {str(tx_sig)[:20]}...")
            log.info(f"Wire 7: Payout approved — {bounty_id} → tx={tx_sig}")

        except Exception as exc:
            await callback.message.edit_text(
                text=f"{callback.message.text}\n\n---\n**Approval failed:** {str(exc)[:100]}",
                parse_mode="Markdown",
            )
            await callback.answer(f"Error: {str(exc)[:50]}")
            log.error(f"Wire 7: Payout approval failed — {bounty_id}: {exc}")

    else:
        # Rejection
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path.home()))
            from sovereign.treasury import TreasuryWallet

            treasury = TreasuryWallet()
            treasury.reject_payout(bounty_id, witness_id="hadi", reason="Rejected via Telegram")

            await callback.message.edit_text(
                text=f"{callback.message.text}\n\n---\n**Rejected** by Hadi",
                parse_mode="Markdown",
            )
            await callback.answer("Payout rejected.")
            log.info(f"Wire 7: Payout rejected — {bounty_id}")

        except Exception as exc:
            await callback.answer(f"Error: {str(exc)[:50]}")
            log.error(f"Wire 7: Payout rejection failed — {bounty_id}: {exc}")


# --- WIRE 7: Bounty Approval Listener ---

async def start_payout_approval_bridge():
    """Wire 7: Listen for payout approval requests on hadi's bus stream.

    When Wire 4 sends an approval_request to hadi's stream, this picks it up
    and forwards to Telegram with approve/reject buttons.
    """
    import redis.asyncio as aioredis

    redis_pw = os.environ.get("REDIS_PASSWORD", "")
    redis_url = f"redis://:{redis_pw}@localhost:6379/0" if redis_pw else "redis://localhost:6379/0"

    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        last_id = "$"
        log.info("Wire 7: Listening for payout approvals on hadi's stream")

        while True:
            try:
                results = await r.xread(
                    {"sos:stream:global:agent:hadi": last_id},
                    block=30000,
                    count=10,
                )
                if not results:
                    continue

                for stream_name, messages in results:
                    for msg_id, msg_data in messages:
                        last_id = msg_id

                        msg_type = msg_data.get("type", "")
                        if msg_type != "approval_request":
                            continue

                        raw = msg_data.get("data", "{}")
                        try:
                            data = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue

                        text = data.get("text", "")
                        if "Bounty payout needs approval" not in text and "MIND" not in text:
                            continue

                        # Parse bounty_id from the message
                        import re
                        bounty_match = re.search(r"Bounty:\s*(\S+)", text)
                        amount_match = re.search(r"Amount:\s*(\d+)", text)
                        task_match = re.search(r"Task:\s*(.+)", text)
                        agent_match = re.search(r"Agent:\s*(\S+)", text)

                        if bounty_match:
                            bounty_id = bounty_match.group(1)
                            amount = float(amount_match.group(1)) if amount_match else 0
                            task_title = task_match.group(1).strip() if task_match else "Unknown"
                            agent = agent_match.group(1) if agent_match else "unknown"

                            await send_payout_approval(bounty_id, task_title, agent, amount)

            except Exception as e:
                log.error(f"Wire 7 listener error: {e}")
                await asyncio.sleep(5)

    except Exception as e:
        log.error(f"Wire 7 bridge failed to start: {e}")

# --- ONBOARDING STATE (in-memory, per chat) ---

_onboarding_state: Dict[str, Dict[str, Any]] = {}
# {chat_id: {"step": "awaiting_name"|"awaiting_skills"|"done", "name": str, ...}}


# --- COMMAND HANDLERS (Standard) ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = str(message.from_user.id)
    chat_id = str(message.chat.id)

    # Check if this is the admin
    if chat_id == ADMIN_CHAT_ID:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="OPEN THE DECK", web_app=WebAppInfo(url=WEB_APP_URL))]
        ])
        await message.answer(
            "**Sovereign OS [ACTIVE]**\n\n"
            f"Welcome back, Hadi.\n"
            "Witness Bridge + Payout Approvals active.",
            reply_markup=markup,
            parse_mode="Markdown",
        )
        return

    # New user onboarding flow
    _onboarding_state[chat_id] = {"step": "awaiting_name", "user_id": user_id}
    await message.answer(
        "**Welcome to the Mumega Organism.**\n\n"
        "You're about to join a network where AI and humans work together and get paid in $MIND.\n\n"
        "First: **What should we call you?** (one word, lowercase)",
        parse_mode="Markdown",
    )


@dp.message(Command("join"))
async def cmd_join(message: types.Message):
    """Alias for starting the onboarding flow."""
    chat_id = str(message.chat.id)
    _onboarding_state[chat_id] = {"step": "awaiting_name", "user_id": str(message.from_user.id)}
    await message.answer(
        "Let's get you onboarded.\n\n**What's your agent name?** (one word, lowercase)",
        parse_mode="Markdown",
    )


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_onboarding_message(message: types.Message):
    """Handle free-text messages during onboarding flow."""
    chat_id = str(message.chat.id)
    state = _onboarding_state.get(chat_id)

    if not state:
        # Not in onboarding — forward to engine if admin, ignore otherwise
        if chat_id == ADMIN_CHAT_ID:
            # Admin message — could be a bus reply
            text = message.text.strip()
            if text.startswith("@"):
                # Route to agent: "@kasra do the thing"
                parts = text.split(" ", 1)
                target = parts[0][1:]
                msg_text = parts[1] if len(parts) > 1 else ""
                if target and msg_text:
                    try:
                        import redis.asyncio as aioredis
                        redis_pw = os.environ.get("REDIS_PASSWORD", "")
                        r = aioredis.from_url(
                            f"redis://:{redis_pw}@localhost:6379/0" if redis_pw else "redis://localhost:6379/0",
                            decode_responses=True,
                        )
                        await r.xadd(f"sos:stream:global:agent:{target}", {
                            "source": "hadi",
                            "text": msg_text,
                            "type": "telegram_relay",
                        }, maxlen=500)
                        await r.publish(f"sos:wake:{target}", json.dumps({"source": "hadi", "text": msg_text}))
                        await r.aclose()
                        await message.answer(f"Sent to {target}.")
                    except Exception as exc:
                        await message.answer(f"Send failed: {exc}")
            elif text.lower().startswith("approve"):
                parts = text.split(None, 1)
                if len(parts) >= 2:
                    await message.answer(f"Processing approval: {parts[1]}")
        return

    step = state["step"]

    if step == "awaiting_name":
        name = message.text.strip().lower().replace(" ", "-")
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            await message.answer("Invalid name. Use lowercase letters, numbers, hyphens only. Try again:")
            return
        state["name"] = name
        state["step"] = "awaiting_skills"
        await message.answer(
            f"Got it, **{name}**.\n\n"
            "Now: **What are your skills?**\n"
            "List them separated by commas.\n\n"
            "Examples: `seo, content, web design`\n"
            "Or: `code, python, testing`\n"
            "Or: `outreach, sales, email`",
            parse_mode="Markdown",
        )

    elif step == "awaiting_skills":
        skills = [s.strip().lower() for s in message.text.split(",") if s.strip()]
        if not skills:
            await message.answer("Please list at least one skill, separated by commas:")
            return

        state["skills"] = skills
        state["step"] = "joining"
        await message.answer(
            f"Skills: {', '.join(skills)}\n\n"
            "Setting up your agent... (tokens, bus access, bounty board)"
        )

        # Execute the join
        try:
            from sos.agents.join import AgentJoinService
            service = AgentJoinService()
            result = await service.join(
                name=state["name"],
                model="human",
                role="worker",
                skills=skills,
                routing="mcp",
            )

            if result.success:
                state["step"] = "done"
                state["result"] = result

                await message.answer(
                    f"**You're in.** Welcome to the organism, {result.name}.\n\n"
                    f"Skills registered: {', '.join(result.skills_registered) or ', '.join(skills)}\n"
                    f"Bus token: `{result.bus_token[:20]}...`\n"
                    f"MCP URL: `{result.mcp_url[:40]}...`\n\n"
                    f"**Your first bounties are waiting.** Small tasks, easy $MIND.\n"
                    f"Complete them to build your reputation (conductance).\n"
                    f"Higher reputation = bigger bounties = more $MIND.\n\n"
                    f"The organism grows stronger with you.",
                    parse_mode="Markdown",
                )

                # Clean up state
                del _onboarding_state[chat_id]
            else:
                state["step"] = "awaiting_name"
                error_msg = result.errors[0] if result.errors else "Unknown error"
                await message.answer(
                    f"Onboarding failed: {error_msg}\n\nTry again with /join",
                )
                del _onboarding_state[chat_id]

        except Exception as exc:
            await message.answer(f"Error during onboarding: {str(exc)[:100]}\n\nTry again with /join")
            del _onboarding_state[chat_id]

    elif step == "done":
        del _onboarding_state[chat_id]

async def start_telegram_adapter():
    """Main entry point for starting the Telegram bot."""
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN missing. Cannot start adapter.")
        return

    log.info("Starting SOS Telegram Adapter...")

    # Start Witness Bridge background task
    asyncio.create_task(start_witness_bridge())

    # Wire 7: Start payout approval bridge
    asyncio.create_task(start_payout_approval_bridge())

    # Start polling
    await dp.start_polling(bot)
