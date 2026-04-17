"""
SOS Identity Core - Guild and Profile Management.

Architecture:
- Persistence: SQLite (via SQLModel/Pydantic) for portability.
- Integration: Updates Redis Bus subscriptions upon Guild Join.
"""

import sqlite3
import json
import secrets
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from sos.kernel import Config
from sos.kernel.identity import UserIdentity, Guild, IdentityType
from sos.kernel.capability import CapabilityAction, create_capability
from sos.kernel.bus import get_bus
from sos.observability.logging import get_logger

log = get_logger("identity_core")

class IdentityCore:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.db_path = self.config.paths.data_dir / "identity.db"
        self.bus = get_bus()
        self._init_db()

    def _init_db(self):
        """Initialize SQLite schema."""
        self.config.paths.data_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            # Users Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    bio TEXT,
                    avatar_url TEXT,
                    level INTEGER DEFAULT 1,
                    xp INTEGER DEFAULT 0,
                    metadata TEXT,
                    created_at TEXT
                )
            """)
            # Guilds Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guilds (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    owner_id TEXT,
                    description TEXT,
                    metadata TEXT,
                    created_at TEXT
                )
            """)
            # Memberships Table (Many-to-Many)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memberships (
                    guild_id TEXT,
                    user_id TEXT,
                    role TEXT,
                    joined_at TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            # Pairing Requests Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pairings (
                    code TEXT PRIMARY KEY,
                    channel TEXT,
                    sender_id TEXT,
                    agent_id TEXT,
                    issued_at TEXT,
                    expires_at TEXT,
                    status TEXT,
                    approved_by TEXT,
                    approved_at TEXT
                )
            """)
            # Allowlist Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS allowlists (
                    channel TEXT,
                    sender_id TEXT,
                    agent_id TEXT,
                    added_at TEXT,
                    added_by TEXT,
                    PRIMARY KEY (channel, sender_id, agent_id)
                )
            """)
            log.info(f"Identity DB initialized at {self.db_path}")

    # --- USER PROFILE OPERATIONS ---

    def create_user(self, name: str, bio: str = "", avatar: str = None) -> UserIdentity:
        user = UserIdentity(name=name, bio=bio, avatar_url=avatar)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO users (id, name, bio, avatar_url, level, xp, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user.id, user.name, user.bio, user.avatar_url, user.level, user.xp, json.dumps(user.metadata), user.created_at.isoformat())
            )
        log.info(f"Created user: {user.id}")
        return user

    def get_user(self, user_id: str) -> Optional[UserIdentity]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row: return None
            
            user = UserIdentity(name=row[1])
            user.id = row[0]
            user.bio = row[2]
            user.avatar_url = row[3]
            user.level = row[4]
            user.xp = row[5]
            user.metadata = json.loads(row[6])
            
            # Fetch Guilds
            guilds = conn.execute("SELECT guild_id FROM memberships WHERE user_id = ?", (user_id,)).fetchall()
            user.guilds = [g[0] for g in guilds]
            
            return user

    # --- GUILD OPERATIONS ---

    async def create_guild(self, name: str, owner_id: str, description: str = "") -> Guild:
        """Create a new Guild and assign owner."""
        guild = Guild(name=name, owner_id=owner_id, description=description)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO guilds (id, name, owner_id, description, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (guild.id, guild.name, guild.owner_id, guild.description, json.dumps(guild.metadata), guild.created_at.isoformat())
            )
            # Add owner as member
            conn.execute(
                "INSERT INTO memberships (guild_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                (guild.id, owner_id, "leader", datetime.utcnow().isoformat())
            )
            
        log.info(f"Created Guild: {guild.id} (Owner: {owner_id})")
        
        # Connect Owner to Squad Channel
        await self.bus.connect()
        # Note: In a real system, the client (Agent/UI) subscribes. Here we just log logic.
        
        return guild

    async def join_guild(self, guild_id: str, user_id: str) -> bool:
        """Add user to guild."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO memberships (guild_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, "member", datetime.utcnow().isoformat())
                )
            log.info(f"User {user_id} joined {guild_id}")
            return True
        except sqlite3.IntegrityError:
            log.warning(f"User {user_id} already in {guild_id}")
            return False

    def list_members(self, guild_id: str) -> List[Dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.name, m.role 
                FROM users u 
                JOIN memberships m ON u.id = m.user_id 
                WHERE m.guild_id = ?
                """, 
                (guild_id,)
            ).fetchall()
            return [{"id": r[0], "name": r[1], "role": r[2]} for r in rows]

    # --- PAIRING / ALLOWLIST OPERATIONS ---

    def create_pairing(
        self,
        channel: str,
        sender_id: str,
        agent_id: str,
        expires_minutes: int = 10,
    ) -> Dict[str, Any]:
        """
        Create a short-lived pairing code for a channel sender.
        """
        code = secrets.token_urlsafe(6)
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=expires_minutes)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO pairings
                (code, channel, sender_id, agent_id, issued_at, expires_at, status, approved_by, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    channel,
                    sender_id,
                    agent_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                    "pending",
                    None,
                    None,
                ),
            )

        return {
            "code": code,
            "channel": channel,
            "sender_id": sender_id,
            "agent_id": agent_id,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "status": "pending",
        }

    def approve_pairing(
        self,
        channel: str,
        code: str,
        approver_id: str,
    ) -> Dict[str, Any]:
        """
        Approve a pairing code and persist allowlist entry.
        """
        now = datetime.utcnow()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT code, channel, sender_id, agent_id, expires_at, status
                FROM pairings
                WHERE code = ? AND channel = ?
                """,
                (code, channel),
            ).fetchone()

            if not row:
                return {"ok": False, "error": "pairing_not_found"}

            expires_at = datetime.fromisoformat(row[4])
            status = row[5]

            if status != "pending":
                return {"ok": False, "error": "pairing_not_pending"}

            if now > expires_at:
                return {"ok": False, "error": "pairing_expired"}

            sender_id = row[2]
            agent_id = row[3]

            conn.execute(
                """
                INSERT OR IGNORE INTO allowlists
                (channel, sender_id, agent_id, added_at, added_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (channel, sender_id, agent_id, now.isoformat(), approver_id),
            )

            conn.execute(
                """
                UPDATE pairings
                SET status = ?, approved_by = ?, approved_at = ?
                WHERE code = ?
                """,
                ("approved", approver_id, now.isoformat(), code),
            )

        capability = create_capability(
            subject=sender_id,
            action=CapabilityAction.TOOL_EXECUTE,
            resource=f"channel:{channel}:sender:{sender_id}",
            duration_hours=24,
            constraints={"agent_id": agent_id},
            issuer=approver_id,
        )

        return {
            "ok": True,
            "channel": channel,
            "sender_id": sender_id,
            "agent_id": agent_id,
            "approved_by": approver_id,
            "approved_at": now.isoformat(),
            "capability": capability.to_dict(),
        }

    def list_allowlist(self, channel: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT channel, sender_id, agent_id, added_at, added_by
                FROM allowlists
                WHERE channel = ?
                """,
                (channel,),
            ).fetchall()
        return [
            {
                "channel": r[0],
                "sender_id": r[1],
                "agent_id": r[2],
                "added_at": r[3],
                "added_by": r[4],
            }
            for r in rows
        ]

# Singleton
_identity = None
def get_identity_core() -> IdentityCore:
    global _identity
    if _identity is None:
        _identity = IdentityCore()
    return _identity
