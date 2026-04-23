"""
Scout Trust & Reputation System

Provides:
- Trust scoring for wisdom sources (scouts)
- Reputation tracking over time
- Verification requirements based on trust level
- Witness requirements for high-value operations
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any
import sqlite3

from sovereign.config import get_default_data_dir

logger = logging.getLogger("trust")


class TrustLevel(Enum):
    """Trust levels for scouts."""
    UNKNOWN = "unknown"       # New scout, no history
    SUSPICIOUS = "suspicious"  # Low trust, requires witness
    PROVISIONAL = "provisional"  # Building trust
    TRUSTED = "trusted"       # Good track record
    VERIFIED = "verified"     # High trust, auto-approve


@dataclass
class TrustScore:
    """Trust score for a scout."""
    scout_id: str
    level: TrustLevel = TrustLevel.UNKNOWN
    score: float = 0.5  # 0.0 to 1.0
    contributions: int = 0
    successful: int = 0
    flagged: int = 0
    last_contribution: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.contributions == 0:
            return 0.0
        return self.successful / self.contributions

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        d = asdict(self)
        d['level'] = self.level.value
        d['success_rate'] = self.success_rate
        return d


@dataclass
class VerificationRequirement:
    """What's required to accept wisdom from a scout."""
    requires_witness: bool = False
    min_witnesses: int = 0
    requires_review: bool = False
    auto_approve: bool = False
    max_value: float = 100.0  # Max MIND reward without witness
    cooldown_seconds: int = 0  # Min time between contributions


class TrustConfig:
    """Configuration for trust system."""

    # Score thresholds for trust levels
    THRESHOLDS = {
        TrustLevel.SUSPICIOUS: 0.2,
        TrustLevel.PROVISIONAL: 0.4,
        TrustLevel.TRUSTED: 0.7,
        TrustLevel.VERIFIED: 0.9,
    }

    # Minimum contributions for each level
    MIN_CONTRIBUTIONS = {
        TrustLevel.SUSPICIOUS: 0,
        TrustLevel.PROVISIONAL: 3,
        TrustLevel.TRUSTED: 10,
        TrustLevel.VERIFIED: 50,
    }

    # Score adjustments
    SUCCESS_BONUS = 0.02      # Per successful contribution
    FLAG_PENALTY = 0.15       # Per flagged contribution
    DECAY_RATE = 0.001        # Daily decay if inactive
    INITIAL_SCORE = 0.5       # Starting score for new scouts

    # Verification requirements per level
    REQUIREMENTS = {
        TrustLevel.UNKNOWN: VerificationRequirement(
            requires_witness=True,
            min_witnesses=2,
            requires_review=True,
            auto_approve=False,
            max_value=10.0,
            cooldown_seconds=3600  # 1 hour
        ),
        TrustLevel.SUSPICIOUS: VerificationRequirement(
            requires_witness=True,
            min_witnesses=1,
            requires_review=True,
            auto_approve=False,
            max_value=25.0,
            cooldown_seconds=1800  # 30 min
        ),
        TrustLevel.PROVISIONAL: VerificationRequirement(
            requires_witness=False,
            min_witnesses=0,
            requires_review=True,
            auto_approve=False,
            max_value=50.0,
            cooldown_seconds=300  # 5 min
        ),
        TrustLevel.TRUSTED: VerificationRequirement(
            requires_witness=False,
            min_witnesses=0,
            requires_review=False,
            auto_approve=True,
            max_value=200.0,
            cooldown_seconds=60  # 1 min
        ),
        TrustLevel.VERIFIED: VerificationRequirement(
            requires_witness=False,
            min_witnesses=0,
            requires_review=False,
            auto_approve=True,
            max_value=1000.0,
            cooldown_seconds=0
        ),
    }

    # High-value thresholds (always require witness)
    HIGH_VALUE_THRESHOLD = 500.0  # MIND


class TrustDatabase:
    """SQLite-based trust score storage."""

    def __init__(self, db_path: str = None):
        if db_path:
            self.db_path = db_path
        else:
            # TD-019: Use MUMEGA_DATA_DIR instead of hardcoded path
            self.db_path = str(get_default_data_dir() / "trust.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scouts (
                    scout_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL DEFAULT 'unknown',
                    score REAL NOT NULL DEFAULT 0.5,
                    contributions INTEGER DEFAULT 0,
                    successful INTEGER DEFAULT 0,
                    flagged INTEGER DEFAULT 0,
                    last_contribution TEXT,
                    created_at TEXT,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contributions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scout_id TEXT NOT NULL,
                    wisdom_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    confidence REAL,
                    reward_amount REAL,
                    status TEXT DEFAULT 'pending',
                    witnesses TEXT DEFAULT '[]',
                    review_notes TEXT,
                    FOREIGN KEY (scout_id) REFERENCES scouts(scout_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS witnesses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contribution_id INTEGER NOT NULL,
                    witness_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    approved BOOLEAN NOT NULL,
                    notes TEXT,
                    FOREIGN KEY (contribution_id) REFERENCES contributions(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scout_id ON contributions(scout_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wisdom_id ON contributions(wisdom_id)")
            conn.commit()

    def get_scout(self, scout_id: str) -> Optional[TrustScore]:
        """Get scout trust score."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM scouts WHERE scout_id = ?",
                (scout_id,)
            ).fetchone()

            if row:
                return TrustScore(
                    scout_id=row['scout_id'],
                    level=TrustLevel(row['level']),
                    score=row['score'],
                    contributions=row['contributions'],
                    successful=row['successful'],
                    flagged=row['flagged'],
                    last_contribution=row['last_contribution'],
                    created_at=row['created_at'],
                    metadata=json.loads(row['metadata'] or '{}')
                )
        return None

    def upsert_scout(self, trust: TrustScore):
        """Insert or update scout trust score."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO scouts (scout_id, level, score, contributions, successful, flagged, last_contribution, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scout_id) DO UPDATE SET
                    level = excluded.level,
                    score = excluded.score,
                    contributions = excluded.contributions,
                    successful = excluded.successful,
                    flagged = excluded.flagged,
                    last_contribution = excluded.last_contribution,
                    metadata = excluded.metadata
            """, (
                trust.scout_id,
                trust.level.value,
                trust.score,
                trust.contributions,
                trust.successful,
                trust.flagged,
                trust.last_contribution,
                trust.created_at,
                json.dumps(trust.metadata)
            ))
            conn.commit()

    def record_contribution(
        self,
        scout_id: str,
        wisdom_id: str,
        confidence: float,
        reward_amount: float,
        status: str = "pending"
    ) -> int:
        """Record a wisdom contribution."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO contributions (scout_id, wisdom_id, timestamp, confidence, reward_amount, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                scout_id,
                wisdom_id,
                datetime.utcnow().isoformat(),
                confidence,
                reward_amount,
                status
            ))
            conn.commit()
            return cursor.lastrowid

    def add_witness(
        self,
        contribution_id: int,
        witness_id: str,
        approved: bool,
        notes: str = None
    ):
        """Add witness approval/rejection."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO witnesses (contribution_id, witness_id, timestamp, approved, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (
                contribution_id,
                witness_id,
                datetime.utcnow().isoformat(),
                approved,
                notes
            ))
            conn.commit()

    def get_witnesses(self, contribution_id: int) -> List[Dict]:
        """Get all witnesses for a contribution."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM witnesses WHERE contribution_id = ?",
                (contribution_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def update_contribution_status(self, contribution_id: int, status: str):
        """Update contribution status."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE contributions SET status = ? WHERE id = ?",
                (status, contribution_id)
            )
            conn.commit()

    def get_recent_contributions(self, scout_id: str, hours: int = 24) -> List[Dict]:
        """Get recent contributions from a scout."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM contributions
                WHERE scout_id = ? AND timestamp > ?
                ORDER BY timestamp DESC
            """, (scout_id, cutoff)).fetchall()
            return [dict(row) for row in rows]


class ScoutTrustManager:
    """Main trust management interface."""

    def __init__(self, db_path: str = None):
        self.db = TrustDatabase(db_path)
        self.config = TrustConfig()

    def get_trust(self, scout_id: str) -> TrustScore:
        """Get or create trust score for a scout."""
        trust = self.db.get_scout(scout_id)
        if not trust:
            trust = TrustScore(
                scout_id=scout_id,
                level=TrustLevel.UNKNOWN,
                score=TrustConfig.INITIAL_SCORE
            )
            self.db.upsert_scout(trust)
            logger.info(f"New scout registered: {scout_id} (UNKNOWN)")
        return trust

    def get_requirements(self, scout_id: str, reward_amount: float = 0) -> VerificationRequirement:
        """Get verification requirements for a scout's contribution."""
        trust = self.get_trust(scout_id)
        req = TrustConfig.REQUIREMENTS[trust.level]

        # High-value transactions always require witness
        if reward_amount > TrustConfig.HIGH_VALUE_THRESHOLD:
            return VerificationRequirement(
                requires_witness=True,
                min_witnesses=max(req.min_witnesses, 2),
                requires_review=True,
                auto_approve=False,
                max_value=req.max_value,
                cooldown_seconds=req.cooldown_seconds
            )

        return req

    def check_cooldown(self, scout_id: str) -> tuple[bool, int]:
        """Check if scout is in cooldown. Returns (allowed, remaining_seconds)."""
        trust = self.get_trust(scout_id)
        req = TrustConfig.REQUIREMENTS[trust.level]

        if req.cooldown_seconds == 0:
            return True, 0

        if not trust.last_contribution:
            return True, 0

        last = datetime.fromisoformat(trust.last_contribution)
        elapsed = (datetime.utcnow() - last).total_seconds()
        remaining = req.cooldown_seconds - elapsed

        if remaining > 0:
            return False, int(remaining)
        return True, 0

    def can_auto_approve(self, scout_id: str, reward_amount: float = 0) -> bool:
        """Check if a contribution can be auto-approved."""
        req = self.get_requirements(scout_id, reward_amount)
        return req.auto_approve and reward_amount <= req.max_value

    def record_contribution(
        self,
        scout_id: str,
        wisdom_id: str,
        confidence: float,
        reward_amount: float
    ) -> Dict[str, Any]:
        """Record a new contribution and return verification requirements."""
        trust = self.get_trust(scout_id)
        req = self.get_requirements(scout_id, reward_amount)

        # Check cooldown
        allowed, remaining = self.check_cooldown(scout_id)
        if not allowed:
            return {
                "status": "cooldown",
                "remaining_seconds": remaining,
                "message": f"Scout in cooldown. Wait {remaining}s."
            }

        # Determine initial status
        if req.auto_approve and reward_amount <= req.max_value:
            status = "approved"
        elif req.requires_witness:
            status = "pending_witness"
        elif req.requires_review:
            status = "pending_review"
        else:
            status = "approved"

        # Record contribution
        contribution_id = self.db.record_contribution(
            scout_id=scout_id,
            wisdom_id=wisdom_id,
            confidence=confidence,
            reward_amount=reward_amount,
            status=status
        )

        # Update scout's last contribution time
        trust.last_contribution = datetime.utcnow().isoformat()
        trust.contributions += 1
        self.db.upsert_scout(trust)

        return {
            "status": status,
            "contribution_id": contribution_id,
            "requires_witness": req.requires_witness,
            "min_witnesses": req.min_witnesses,
            "trust_level": trust.level.value,
            "trust_score": trust.score
        }

    def add_witness_approval(
        self,
        contribution_id: int,
        witness_id: str,
        approved: bool,
        notes: str = None
    ) -> Dict[str, Any]:
        """Add witness approval and check if contribution is now approved."""
        self.db.add_witness(contribution_id, witness_id, approved, notes)

        # Get all witnesses
        witnesses = self.db.get_witnesses(contribution_id)
        approvals = sum(1 for w in witnesses if w['approved'])
        rejections = sum(1 for w in witnesses if not w['approved'])

        # Get contribution to check requirements
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            contrib = conn.execute(
                "SELECT * FROM contributions WHERE id = ?",
                (contribution_id,)
            ).fetchone()

        if not contrib:
            return {"error": "Contribution not found"}

        scout_id = contrib['scout_id']
        reward = contrib['reward_amount']
        req = self.get_requirements(scout_id, reward)

        result = {
            "approvals": approvals,
            "rejections": rejections,
            "required": req.min_witnesses
        }

        # Check if enough approvals
        if approvals >= req.min_witnesses:
            self.db.update_contribution_status(contribution_id, "approved")
            self._update_trust_score(scout_id, success=True)
            result["status"] = "approved"
            result["message"] = "Contribution approved by witnesses"

        # Check if too many rejections
        elif rejections >= 2:  # 2 rejections = rejected
            self.db.update_contribution_status(contribution_id, "rejected")
            self._update_trust_score(scout_id, success=False, flagged=True)
            result["status"] = "rejected"
            result["message"] = "Contribution rejected by witnesses"

        else:
            result["status"] = "pending_witness"
            result["message"] = f"Need {req.min_witnesses - approvals} more approvals"

        return result

    def flag_contribution(self, scout_id: str, wisdom_id: str, reason: str):
        """Flag a contribution as problematic."""
        self._update_trust_score(scout_id, success=False, flagged=True)
        logger.warning(f"Flagged contribution from {scout_id}: {reason}")

    def confirm_success(self, scout_id: str, wisdom_id: str):
        """Confirm a contribution was valuable."""
        self._update_trust_score(scout_id, success=True)
        logger.info(f"Confirmed valuable contribution from {scout_id}")

    def _update_trust_score(self, scout_id: str, success: bool, flagged: bool = False):
        """Update trust score based on contribution outcome."""
        trust = self.get_trust(scout_id)

        if success:
            trust.successful += 1
            trust.score = min(1.0, trust.score + TrustConfig.SUCCESS_BONUS)
        else:
            if flagged:
                trust.flagged += 1
                trust.score = max(0.0, trust.score - TrustConfig.FLAG_PENALTY)

        # Recalculate trust level
        trust.level = self._calculate_level(trust)
        self.db.upsert_scout(trust)

        logger.info(
            f"Updated trust for {scout_id}: "
            f"score={trust.score:.2f}, level={trust.level.value}"
        )

    def _calculate_level(self, trust: TrustScore) -> TrustLevel:
        """Calculate trust level based on score and contributions."""
        # Check from highest to lowest
        for level in [TrustLevel.VERIFIED, TrustLevel.TRUSTED, TrustLevel.PROVISIONAL, TrustLevel.SUSPICIOUS]:
            if (trust.score >= TrustConfig.THRESHOLDS[level] and
                trust.contributions >= TrustConfig.MIN_CONTRIBUTIONS[level]):
                return level

        return TrustLevel.UNKNOWN

    def get_stats(self) -> Dict[str, Any]:
        """Get overall trust system statistics."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row

            scouts = conn.execute("SELECT COUNT(*), level FROM scouts GROUP BY level").fetchall()
            contributions = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                       SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                       SUM(CASE WHEN status LIKE 'pending%' THEN 1 ELSE 0 END) as pending
                FROM contributions
            """).fetchone()

            return {
                "scouts_by_level": {row['level']: row['COUNT(*)'] for row in scouts},
                "contributions": {
                    "total": contributions['total'] or 0,
                    "approved": contributions['approved'] or 0,
                    "rejected": contributions['rejected'] or 0,
                    "pending": contributions['pending'] or 0
                }
            }


# Global instance
_trust_manager: Optional[ScoutTrustManager] = None


def get_trust_manager() -> ScoutTrustManager:
    """Get global trust manager instance."""
    global _trust_manager
    if _trust_manager is None:
        _trust_manager = ScoutTrustManager()
    return _trust_manager
