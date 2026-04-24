"""ProjectMemberService — project membership management."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from sos.services.squad.service import DEFAULT_TENANT_ID, SquadDB, now_iso


TOKENS_PATH = Path(__file__).parent.parent.parent / "bus" / "tokens.json"
ROLE_ORDER = ["observer", "member", "owner"]

# Role that existing customer tokens (no role field) default to
DEFAULT_CUSTOMER_ROLE = "owner"


class MemberNotFoundError(ValueError):
    pass


class InsufficientRoleError(PermissionError):
    def __init__(self, required: str, actual: str):
        self.required = required
        self.actual = actual
        super().__init__(f"Required role '{required}', token has '{actual}'")


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_tokens() -> list[dict]:
    """Load SOS bus tokens from tokens.json. Returns empty list on failure."""
    try:
        return json.loads(TOKENS_PATH.read_text())
    except Exception:
        return []


def lookup_sos_token(raw_token: str) -> Optional[dict]:
    """Validate a SOS bus token. Returns token record or None if invalid/inactive."""
    h = _sha256(raw_token)
    for t in _load_tokens():
        stored = t.get("token_hash", "")
        # token_hash may be plain sha256 hex or prefixed "sha256:..."
        stored_hex = stored.removeprefix("sha256:")
        if stored_hex == h and t.get("active", False):
            return t
    # Also allow direct token match (some legacy entries store plain token)
    for t in _load_tokens():
        if t.get("token") == raw_token and t.get("active", False):
            return t
    return None


def role_satisfies(token_role: str, required_role: str) -> bool:
    """True if token_role >= required_role in owner > member > observer hierarchy."""
    try:
        return ROLE_ORDER.index(token_role) >= ROLE_ORDER.index(required_role)
    except ValueError:
        return False


class ProjectMemberService:
    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    def add_member(
        self,
        project_id: str,
        agent_id: str,
        role: str = "member",
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> dict:
        if role not in ROLE_ORDER:
            raise ValueError(f"Invalid role '{role}'. Must be one of {ROLE_ORDER}")
        added_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_members (project_id, agent_id, tenant_id, role, added_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, agent_id, tenant_id) DO UPDATE SET role = excluded.role
                """,
                (project_id, agent_id, tenant_id, role, added_at),
            )
        return {
            "project_id": project_id,
            "agent_id": agent_id,
            "role": role,
            "tenant_id": tenant_id,
            "added_at": added_at,
        }

    def remove_member(
        self,
        project_id: str,
        agent_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        with self.db.connect() as conn:
            result = conn.execute(
                """
                DELETE FROM project_members
                WHERE project_id = ? AND agent_id = ? AND tenant_id = ?
                """,
                (project_id, agent_id, tenant_id),
            )
            if result.rowcount == 0:
                raise MemberNotFoundError(f"{agent_id} not in project {project_id}")

    def list_members(
        self,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT project_id, agent_id, role, added_at FROM project_members
                WHERE project_id = ? AND tenant_id = ?
                ORDER BY added_at ASC
                """,
                (project_id, tenant_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_role(
        self,
        project_id: str,
        agent_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> Optional[str]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT role FROM project_members WHERE project_id = ? AND agent_id = ? AND tenant_id = ?",
                (project_id, agent_id, tenant_id),
            ).fetchone()
            return row["role"] if row else None
