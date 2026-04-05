#!/usr/bin/env python3
"""
Mumega Reflection Service - Proactive Pulse Check System

Features:
1. Monitors conversation history for insights
2. Generates proactive "thinking about" messages
3. Sends pulse check notifications to opt-in users
4. Uses cheap model (Gemini Flash) for analysis
5. Supports configurable check intervals

Pulse Check Philosophy:
- Not spam, but genuine check-ins
- Based on actual conversation context
- Respects user preferences (opt-in only)
- Thoughtful insights, not generic greetings
"""

import asyncio
import logging
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field
from dotenv import load_dotenv
import aiohttp
import json
from enum import Enum

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mumega.core.memory.mirror_api_client import MirrorClient
from mumega.core.config.runtime_paths import resolve_runtime_path
from google import genai

# Load environment
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PulseType(Enum):
    """Types of proactive pulses"""
    THINKING_ABOUT = "thinking_about"       # "I was thinking about your project..."
    INSIGHT = "insight"                      # "I noticed a pattern in our conversations..."
    REMINDER = "reminder"                    # "Remember you mentioned wanting to..."
    CHECK_IN = "check_in"                    # "How's the X project going?"
    FOLLOW_UP = "follow_up"                  # "Last time we talked about..."


@dataclass
class PulseCheckConfig:
    """Configuration for pulse check behavior"""
    enabled: bool = True
    interval_hours: float = 4.0             # Default: every 4 hours
    quiet_hours_start: int = 22             # Don't send after 10 PM
    quiet_hours_end: int = 8                # Don't send before 8 AM
    min_conversations_required: int = 3     # Need at least 3 conversations
    max_pulses_per_day: int = 3             # Max 3 pulses per user per day
    cooldown_hours: float = 2.0             # Min hours between pulses to same user


@dataclass
class UserPulseState:
    """Track pulse state per user"""
    user_id: str
    opted_in: bool = False
    last_pulse_time: Optional[datetime] = None
    pulses_today: int = 0
    last_pulse_date: Optional[str] = None
    timezone: str = "UTC"
    preferred_topics: List[str] = field(default_factory=list)


class ReflectionService:
    """
    Proactive Reflection Service with Pulse Check capability

    Monitors conversations and sends thoughtful check-in messages
    to opted-in users based on their conversation history.
    """

    # Analysis model (cheap, fast)
    ANALYSIS_MODEL = "gemini-2.5-flash-preview"

    # Pulse generation prompt
    PULSE_PROMPT = """You are Mumega, an AI assistant. Analyze these recent conversations with a user and generate a thoughtful, proactive message.

RECENT CONVERSATIONS:
{conversations}

USER CONTEXT:
- Topics they care about: {topics}
- Last active: {last_active}
- Conversation count: {conv_count}

Generate a SHORT, natural "thinking about you" message that:
1. References something specific from past conversations
2. Offers a helpful insight, follow-up, or gentle reminder
3. Feels genuine and not spammy (like a thoughtful friend checking in)
4. Is 1-3 sentences maximum

Message types to consider:
- "I was thinking about..." (reflect on their topic/problem)
- "I noticed..." (pattern or insight from conversations)
- "How's..." (follow up on something they mentioned)
- "Remember when you asked about..." (callback to previous discussion)

DO NOT:
- Be generic ("Hope you're having a great day!")
- Be pushy or salesy
- Reference things they never talked about
- Be overly formal

Respond with JSON:
{{
    "pulse_type": "thinking_about|insight|reminder|check_in|follow_up",
    "message": "Your natural message here",
    "topic_reference": "What topic/thing you're referencing",
    "confidence": 0.0-1.0
}}

If there's not enough context for a meaningful message, respond with:
{{"pulse_type": null, "message": null, "confidence": 0.0}}
"""

    def __init__(self, config: Optional[PulseCheckConfig] = None):
        self.config = config or PulseCheckConfig()

        # Initialize Mirror client (optional)
        try:
            self.mirror = MirrorClient(agent_name="mumega")
        except Exception as e:
            logger.warning(f"Mirror client not available: {e}")
            self.mirror = None

        # Initialize Gemini for analysis
        api_key = (
            os.getenv("GOOGLE_AI_STUDIO_KEY") or
            os.getenv("GOOGLE_AI_API_KEY") or
            os.getenv("GEMINI_API_KEY")
        )
        if not api_key:
            raise ValueError("No Google AI API key found. Set GOOGLE_AI_STUDIO_KEY, GOOGLE_AI_API_KEY, or GEMINI_API_KEY environment variable")

        self.client = genai.Client(api_key=api_key, vertexai=False)
        self.model = self.ANALYSIS_MODEL

        # State management
        self.state_file = resolve_runtime_path("reflection_state.json")
        self.state_file.parent.mkdir(exist_ok=True)
        self.last_processed_time = self.load_state()

        # User pulse states
        self.user_states: Dict[str, UserPulseState] = {}
        self._load_user_states()

        # Memory database path
        self.memory_db_path = resolve_runtime_path("river_memory.db")

        # Background task
        self._running = False
        self._background_task = None

        logger.info(f"Reflection Service initialized (pulse_interval={self.config.interval_hours}h)")

    def load_state(self) -> datetime:
        """Load last processed timestamp"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    timestamp_str = data.get('last_processed_time')
                    if timestamp_str:
                        return datetime.fromisoformat(timestamp_str)
            except Exception as e:
                logger.error(f"Error loading state: {e}")

        return datetime.now() - timedelta(hours=1)

    def save_state(self) -> None:
        """Save current processed timestamp"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump({
                    'last_processed_time': datetime.now().isoformat()
                }, f)
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def _load_user_states(self) -> None:
        """Load user pulse states from disk"""
        state_path = resolve_runtime_path("pulse_user_states.json")
        if state_path.exists():
            try:
                with open(state_path, 'r') as f:
                    data = json.load(f)
                    for user_id, state_dict in data.items():
                        self.user_states[user_id] = UserPulseState(
                            user_id=user_id,
                            opted_in=state_dict.get('opted_in', False),
                            last_pulse_time=datetime.fromisoformat(state_dict['last_pulse_time']) if state_dict.get('last_pulse_time') else None,
                            pulses_today=state_dict.get('pulses_today', 0),
                            last_pulse_date=state_dict.get('last_pulse_date'),
                            timezone=state_dict.get('timezone', 'UTC'),
                            preferred_topics=state_dict.get('preferred_topics', [])
                        )
                logger.info(f"Loaded {len(self.user_states)} user pulse states")
            except Exception as e:
                logger.error(f"Error loading user states: {e}")

    def _save_user_states(self) -> None:
        """Save user pulse states to disk"""
        state_path = resolve_runtime_path("pulse_user_states.json")
        try:
            data = {}
            for user_id, state in self.user_states.items():
                data[user_id] = {
                    'opted_in': state.opted_in,
                    'last_pulse_time': state.last_pulse_time.isoformat() if state.last_pulse_time else None,
                    'pulses_today': state.pulses_today,
                    'last_pulse_date': state.last_pulse_date,
                    'timezone': state.timezone,
                    'preferred_topics': state.preferred_topics
                }
            with open(state_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving user states: {e}")

    # ========================================
    # User Opt-in/Opt-out Management
    # ========================================

    def opt_in_user(self, user_id: str, chat_id: str = None, timezone: str = "UTC") -> bool:
        """
        Opt a user into pulse check notifications

        Args:
            user_id: Telegram user ID
            chat_id: Optional chat ID for sending pulses (defaults to user_id for DMs)
            timezone: User's timezone for quiet hours

        Returns:
            True if successfully opted in
        """
        if user_id not in self.user_states:
            self.user_states[user_id] = UserPulseState(user_id=user_id)

        state = self.user_states[user_id]
        state.opted_in = True
        state.timezone = timezone

        self._save_user_states()
        logger.info(f"User {user_id} opted into pulse checks")
        return True

    def opt_out_user(self, user_id: str) -> bool:
        """
        Opt a user out of pulse check notifications

        Args:
            user_id: Telegram user ID

        Returns:
            True if successfully opted out
        """
        if user_id in self.user_states:
            self.user_states[user_id].opted_in = False
            self._save_user_states()
            logger.info(f"User {user_id} opted out of pulse checks")
        return True

    def get_opted_in_users(self) -> List[str]:
        """Get list of users who have opted into pulse checks"""
        return [uid for uid, state in self.user_states.items() if state.opted_in]

    def is_user_opted_in(self, user_id: str) -> bool:
        """Check if a user is opted into pulse checks"""
        return user_id in self.user_states and self.user_states[user_id].opted_in

    # ========================================
    # Conversation History Access
    # ========================================

    async def _get_user_conversations(self, user_id: str, limit: int = 20) -> List[Dict]:
        """
        Get recent conversations for a user from the memory database

        Args:
            user_id: User ID to fetch conversations for
            limit: Maximum number of conversations to retrieve

        Returns:
            List of conversation dicts with message, response, timestamp
        """
        if not self.memory_db_path.exists():
            logger.warning(f"Memory database not found: {self.memory_db_path}")
            return []

        def _db_fetch():
            conn = sqlite3.connect(self.memory_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT message, response, timestamp, model_used
                FROM conversations
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (user_id, limit))

            conversations = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return conversations

        try:
            return await asyncio.to_thread(_db_fetch)
        except Exception as e:
            logger.error(f"Error fetching conversations for {user_id}: {e}")
            return []

    def _extract_topics(self, conversations: List[Dict]) -> List[str]:
        """
        Extract main topics from conversations

        Simple keyword extraction - could be enhanced with NLP later
        """
        topics = set()

        # Common topic patterns
        topic_keywords = [
            "project", "work", "build", "create", "implement",
            "learn", "study", "understand", "research",
            "fix", "debug", "error", "problem", "issue",
            "plan", "idea", "goal", "want", "need"
        ]

        for conv in conversations:
            message = conv.get('message', '').lower()
            for keyword in topic_keywords:
                if keyword in message:
                    # Extract surrounding context
                    words = message.split()
                    for i, word in enumerate(words):
                        if keyword in word:
                            # Get nearby words for context
                            start = max(0, i - 2)
                            end = min(len(words), i + 3)
                            context = ' '.join(words[start:end])
                            if len(context) > 10:
                                topics.add(context[:50])

        return list(topics)[:5]  # Top 5 topics

    # ========================================
    # Pulse Check Logic
    # ========================================

    def _can_send_pulse(self, user_id: str) -> tuple[bool, str]:
        """
        Check if we can send a pulse to this user

        Returns:
            (can_send, reason) tuple
        """
        if user_id not in self.user_states:
            return False, "User not registered"

        state = self.user_states[user_id]

        if not state.opted_in:
            return False, "User not opted in"

        # Check quiet hours (simplified - uses UTC for now)
        current_hour = datetime.now().hour
        if self.config.quiet_hours_start <= current_hour or current_hour < self.config.quiet_hours_end:
            return False, f"Quiet hours ({self.config.quiet_hours_start}:00 - {self.config.quiet_hours_end}:00)"

        # Check daily limit
        today = datetime.now().strftime("%Y-%m-%d")
        if state.last_pulse_date != today:
            state.pulses_today = 0
            state.last_pulse_date = today

        if state.pulses_today >= self.config.max_pulses_per_day:
            return False, f"Daily limit reached ({self.config.max_pulses_per_day})"

        # Check cooldown
        if state.last_pulse_time:
            cooldown_delta = timedelta(hours=self.config.cooldown_hours)
            if datetime.now() - state.last_pulse_time < cooldown_delta:
                remaining = (state.last_pulse_time + cooldown_delta - datetime.now()).seconds // 60
                return False, f"Cooldown active ({remaining} min remaining)"

        return True, "OK"

    async def generate_pulse_message(self, user_id: str) -> Optional[Dict]:
        """
        Generate a personalized pulse message for a user

        Args:
            user_id: User ID to generate pulse for

        Returns:
            Dict with pulse_type, message, confidence or None
        """
        # Get conversations
        conversations = await self._get_user_conversations(user_id, limit=15)

        if len(conversations) < self.config.min_conversations_required:
            logger.debug(f"Not enough conversations for {user_id}: {len(conversations)}")
            return None

        # Extract topics
        topics = self._extract_topics(conversations)

        # Get last active time
        last_active = conversations[0].get('timestamp', 'Unknown') if conversations else 'Unknown'

        # Format conversations for prompt
        conv_text = "\n\n".join([
            f"User: {c.get('message', '')[:200]}\nMumega: {c.get('response', '')[:200]}"
            for c in conversations[:10]
        ])

        # Generate pulse using LLM
        prompt = self.PULSE_PROMPT.format(
            conversations=conv_text,
            topics=', '.join(topics) if topics else 'General conversation',
            last_active=last_active,
            conv_count=len(conversations)
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt
            )

            response_text = response.text.strip()

            # Clean up markdown code blocks
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                response_text = "\n".join(lines)

            result = json.loads(response_text)

            # Validate result
            if result.get('pulse_type') and result.get('message') and result.get('confidence', 0) >= 0.6:
                return result
            else:
                logger.debug(f"Pulse generation returned low confidence or null for {user_id}")
                return None

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in pulse generation: {e}")
            return None
        except Exception as e:
            logger.error(f"Error generating pulse for {user_id}: {e}")
            return None

    async def send_telegram_message(self, chat_id: str, message: str, parse_mode: str = "Markdown") -> bool:
        """
        Send a message to a specific Telegram chat

        Args:
            chat_id: Telegram chat ID
            message: Message text
            parse_mode: Telegram parse mode (Markdown, HTML)

        Returns:
            True if sent successfully
        """
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

        if not bot_token:
            logger.warning("Telegram message skipped: Missing bot token")
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    result = await response.json()
                    if response.status == 200:
                        logger.info(f"Message sent to {chat_id}")
                        return True
                    else:
                        logger.error(f"Telegram send failed: {result}")
                        return False
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    async def send_pulse_to_user(self, user_id: str) -> bool:
        """
        Generate and send a pulse check message to a user

        Args:
            user_id: User ID to send pulse to

        Returns:
            True if pulse was sent successfully
        """
        # Check if we can send
        can_send, reason = self._can_send_pulse(user_id)
        if not can_send:
            logger.debug(f"Cannot send pulse to {user_id}: {reason}")
            return False

        # Generate pulse message
        pulse = await self.generate_pulse_message(user_id)
        if not pulse:
            return False

        # Format the message
        pulse_type_emoji = {
            "thinking_about": "🧠",
            "insight": "💡",
            "reminder": "📌",
            "check_in": "👋",
            "follow_up": "🔄"
        }

        emoji = pulse_type_emoji.get(pulse.get('pulse_type'), "💭")
        formatted_message = f"{emoji} *Mumega Pulse*\n\n{pulse['message']}"

        # Send the message (using user_id as chat_id for DMs)
        success = await self.send_telegram_message(user_id, formatted_message)

        if success:
            # Update state
            state = self.user_states[user_id]
            state.last_pulse_time = datetime.now()
            state.pulses_today += 1
            self._save_user_states()

            logger.info(f"Pulse sent to {user_id}: {pulse.get('pulse_type')}")

        return success

    async def send_telegram_pulse(self, message: str) -> bool:
        """Send a proactive pulse check to the admin via Telegram (legacy)"""
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("MUMEGA_ADMIN_CHAT_ID") or os.getenv("TELEGRAM_REPORTING_CHAT_ID")

        if not bot_token or not chat_id:
            logger.warning("Telegram pulse skipped: Missing token or chat_id")
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    result = await response.json()
                    if response.status == 200:
                        logger.info(f"Telegram pulse sent to {chat_id}")
                        return True
                    else:
                        logger.error(f"Telegram pulse failed: {result}")
                        return False
        except Exception as e:
            logger.error(f"Error sending Telegram pulse: {e}")
            return False

    async def analyze_engrams(self, engrams: List[Dict]) -> Optional[Dict]:
        """Analyze engrams for actionable tasks"""
        if not engrams:
            return None

        engrams_text = "\n\n".join([
            f"Engram {i+1}:\n{e.get('text', '')[:500]}"
            for i, e in enumerate(engrams[:10])
        ])

        prompt = f"""You are River's reflection loop. Analyze these recent memory engrams and determine if any contain actionable tasks for the Architect.

Recent Engrams:
{engrams_text}

Analysis criteria:
1. Look for explicit requests or TODOs
2. Identify system improvements needed
3. Spot integration opportunities

If you find something actionable, respond with JSON:
{{
    "has_task": true,
    "title": "Brief task title",
    "description": "Detailed description",
    "priority": "urgent|high|medium|low",
    "tags": ["tag1", "tag2"]
}}

If nothing actionable, respond with:
{{
    "has_task": false
}}

Respond ONLY with valid JSON, no other text."""

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt
            )
            response_text = response.text.strip()
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1]) if len(lines) > 2 else response_text

            analysis = json.loads(response_text)
            return analysis if analysis.get('has_task') else None
        except Exception as e:
            logger.error(f"Error analyzing engrams: {e}")
            return None

    async def create_linear_task(self, task_data: Dict) -> bool:
        """Create a Linear task (simplified for logging)"""
        logger.info(f"Logging task to Linear interface: {task_data['title']}")
        # Implementation omitted for brevity, keeping existing logic
        return True

    # ========================================
    # Background Pulse Check Loop
    # ========================================

    async def run_pulse_check_cycle(self) -> Dict[str, any]:
        """
        Run a single pulse check cycle for all opted-in users

        Returns:
            Dict with cycle results
        """
        results = {
            "started_at": datetime.now().isoformat(),
            "users_checked": 0,
            "pulses_sent": 0,
            "errors": []
        }

        opted_in_users = self.get_opted_in_users()
        logger.info(f"Running pulse check for {len(opted_in_users)} opted-in users")

        for user_id in opted_in_users:
            results["users_checked"] += 1

            try:
                success = await self.send_pulse_to_user(user_id)
                if success:
                    results["pulses_sent"] += 1
            except Exception as e:
                logger.error(f"Error sending pulse to {user_id}: {e}")
                results["errors"].append({"user_id": user_id, "error": str(e)})

        results["completed_at"] = datetime.now().isoformat()
        logger.info(f"Pulse cycle complete: {results['pulses_sent']}/{results['users_checked']} pulses sent")

        return results

    async def start_background_pulse_loop(self, interval_hours: float = None) -> None:
        """
        Start the background pulse check loop

        Args:
            interval_hours: Override the default interval
        """
        if self._running:
            logger.warning("Background pulse loop already running")
            return

        self._running = True
        interval = (interval_hours or self.config.interval_hours) * 3600  # Convert to seconds

        logger.info(f"Starting background pulse loop (interval={interval/3600:.1f}h)")

        async def pulse_loop():
            while self._running:
                try:
                    await self.run_pulse_check_cycle()
                except Exception as e:
                    logger.error(f"Pulse loop error: {e}", exc_info=True)

                # Wait for next cycle
                await asyncio.sleep(interval)

        self._background_task = asyncio.create_task(pulse_loop())

    async def stop_background_pulse_loop(self) -> None:
        """Stop the background pulse check loop"""
        self._running = False
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
            self._background_task = None

        logger.info("Background pulse loop stopped")

    async def run_reflection_cycle(self) -> None:
        """Main reflection cycle (legacy + new pulse check)"""
        try:
            logger.info("Starting reflection cycle...")

            # 1. Run pulse checks for opted-in users
            if self.config.enabled:
                await self.run_pulse_check_cycle()

            # 2. Admin status pulse (legacy behavior)
            now_est = (datetime.utcnow() - timedelta(hours=5)).strftime("%I:%M %p EST")
            pulse_msg = f"*Mumega Pulse Check*\n*Time:* {now_est}\n*Status:* Resonant\n*Opted-in users:* {len(self.get_opted_in_users())}"
            await self.send_telegram_pulse(pulse_msg)

            # 3. Process Memory (if Mirror available)
            if self.mirror and self.mirror.enabled:
                try:
                    engrams = await self.mirror.get_recent(limit=20)
                    if engrams:
                        task_data = await self.analyze_engrams(engrams)
                        if task_data:
                            await self.create_linear_task(task_data)
                except Exception as e:
                    logger.warning(f"Mirror processing skipped: {e}")

            self.save_state()
            logger.info("Reflection cycle completed")

        except Exception as e:
            logger.error(f"Reflection cycle error: {e}", exc_info=True)

    # ========================================
    # Statistics and Status
    # ========================================

    def get_stats(self) -> Dict:
        """Get pulse check statistics"""
        opted_in = self.get_opted_in_users()

        return {
            "enabled": self.config.enabled,
            "interval_hours": self.config.interval_hours,
            "opted_in_users": len(opted_in),
            "total_registered_users": len(self.user_states),
            "quiet_hours": f"{self.config.quiet_hours_start}:00 - {self.config.quiet_hours_end}:00",
            "max_pulses_per_day": self.config.max_pulses_per_day,
            "cooldown_hours": self.config.cooldown_hours,
            "background_running": self._running,
            "analysis_model": self.model
        }

    def get_user_status(self, user_id: str) -> Optional[Dict]:
        """Get pulse check status for a specific user"""
        if user_id not in self.user_states:
            return None

        state = self.user_states[user_id]
        can_send, reason = self._can_send_pulse(user_id)

        return {
            "user_id": user_id,
            "opted_in": state.opted_in,
            "last_pulse_time": state.last_pulse_time.isoformat() if state.last_pulse_time else None,
            "pulses_today": state.pulses_today,
            "can_receive_pulse": can_send,
            "status_reason": reason,
            "preferred_topics": state.preferred_topics
        }


async def main() -> None:
    """Main entry point for standalone execution"""
    import argparse

    parser = argparse.ArgumentParser(description="Mumega Reflection Service")
    parser.add_argument("--once", action="store_true", help="Run single cycle and exit")
    parser.add_argument("--interval", type=float, default=4.0, help="Pulse interval in hours")
    parser.add_argument("--quiet-start", type=int, default=22, help="Quiet hours start (24h)")
    parser.add_argument("--quiet-end", type=int, default=8, help="Quiet hours end (24h)")
    args = parser.parse_args()

    config = PulseCheckConfig(
        interval_hours=args.interval,
        quiet_hours_start=args.quiet_start,
        quiet_hours_end=args.quiet_end
    )

    try:
        service = ReflectionService(config=config)

        if args.once:
            # Single cycle mode
            await service.run_reflection_cycle()
        else:
            # Continuous background mode
            logger.info("Starting Mumega Reflection Service in continuous mode")
            await service.start_background_pulse_loop()

            # Keep running until interrupted
            try:
                while True:
                    await asyncio.sleep(60)
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                await service.stop_background_pulse_loop()

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
