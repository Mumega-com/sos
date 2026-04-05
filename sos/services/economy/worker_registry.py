"""
Worker Registry
===============
Local registry for workers/agents who can claim work and receive payouts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from mumega.core.config.runtime_paths import resolve_runtime_path

logger = logging.getLogger(__name__)


@dataclass
class WorkerProfile:
    worker_id: str
    wallet_address: str
    agent_name: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    registered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    total_claimed: int = 0
    total_started: int = 0
    total_submitted: int = 0
    total_verified: int = 0
    total_rejected: int = 0
    total_disputed: int = 0
    total_paid: int = 0
    total_slashed: float = 0.0
    slash_balance: float = 0.0
    last_activity_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Calculate success rate: verified / (verified + rejected)."""
        total = self.total_verified + self.total_rejected
        if total == 0:
            return 0.5  # No history = neutral
        return self.total_verified / total

    @property
    def completion_rate(self) -> float:
        """Calculate completion rate: submitted / claimed."""
        if self.total_claimed == 0:
            return 1.0  # No claims = perfect
        return min(1.0, self.total_submitted / self.total_claimed)

    @property
    def dispute_rate(self) -> float:
        """Calculate dispute rate: disputed / total_submitted."""
        if self.total_submitted == 0:
            return 0.0
        return self.total_disputed / self.total_submitted

    @property
    def reputation_score(self) -> float:
        """
        Calculate overall reputation score (0.0 - 1.0).
        
        Formula:
        - Base: success_rate (50% weight)
        - Completion bonus: completion_rate (30% weight)
        - Dispute penalty: -dispute_rate (20% weight)
        - Experience bonus: log(total_verified + 1) / 10, capped at 0.1
        """
        import math
        
        base = self.success_rate * 0.5
        completion = self.completion_rate * 0.3
        dispute_penalty = self.dispute_rate * 0.2
        experience_bonus = min(0.1, math.log(self.total_verified + 1) / 10)
        
        score = base + completion - dispute_penalty + experience_bonus
        return max(0.0, min(1.0, score))  # Clamp to [0, 1]

    def get_reputation_details(self) -> Dict[str, Any]:
        """Get detailed reputation breakdown."""
        return {
            "worker_id": self.worker_id,
            "reputation_score": round(self.reputation_score, 3),
            "success_rate": round(self.success_rate, 3),
            "completion_rate": round(self.completion_rate, 3),
            "dispute_rate": round(self.dispute_rate, 3),
            "total_verified": self.total_verified,
            "total_rejected": self.total_rejected,
            "total_disputed": self.total_disputed,
            "total_paid": self.total_paid,
            "total_slashed": round(self.total_slashed, 3),
            "slash_balance": round(self.slash_balance, 3),
            "tier": self._calculate_tier(),
        }

    def _calculate_tier(self) -> str:
        """Calculate worker tier based on reputation and volume."""
        score = self.reputation_score
        if score >= 0.85 and self.total_verified >= 50:
            return "MASTER"
        elif score >= 0.7 and self.total_verified >= 20:
            return "EXPERT"
        elif score >= 0.5 and self.total_verified >= 5:
            return "JOURNEYMAN"
        elif self.total_verified >= 1:
            return "APPRENTICE"
        else:
            return "NOVICE"


class WorkerRegistry:
    """Local JSON-backed registry of workers."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path) if storage_path else resolve_runtime_path("work", "workers.json")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._workers: Dict[str, WorkerProfile] = {}
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            workers = data.get("workers", {})
            for worker_id, payload in workers.items():
                self._workers[worker_id] = WorkerProfile(**payload)
        except Exception as exc:
            logger.warning("Failed to load worker registry: %s", exc)

    def _save(self) -> None:
        payload = {"workers": {wid: asdict(profile) for wid, profile in self._workers.items()}}
        with self.storage_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def register_worker(
        self,
        worker_id: str,
        wallet_address: str,
        agent_name: Optional[str] = None,
        roles: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkerProfile:
        profile = WorkerProfile(
            worker_id=worker_id,
            wallet_address=wallet_address,
            agent_name=agent_name,
            roles=roles or [],
            capabilities=capabilities or [],
            metadata=metadata or {},
        )
        self._workers[worker_id] = profile
        self._save()
        return profile

    def get_worker(self, worker_id: str) -> Optional[WorkerProfile]:
        return self._workers.get(worker_id)

    def list_workers(self) -> List[WorkerProfile]:
        return list(self._workers.values())

    def record_event(self, worker_id: str, event: str) -> Optional[WorkerProfile]:
        profile = self._workers.get(worker_id)
        if not profile:
            return None

        counters = {
            "claimed": "total_claimed",
            "started": "total_started",
            "submitted": "total_submitted",
            "verified": "total_verified",
            "rejected": "total_rejected",
            "disputed": "total_disputed",
            "paid": "total_paid",
            "slashed": "total_slashed",
        }
        field_name = counters.get(event)
        if field_name and hasattr(profile, field_name):
            setattr(profile, field_name, getattr(profile, field_name) + 1)
        profile.last_activity_at = datetime.utcnow().isoformat()
        self._workers[worker_id] = profile
        self._save()
        return profile

    def apply_slash(self, worker_id: str, amount: float, metadata: Optional[Dict[str, Any]] = None) -> Optional[WorkerProfile]:
        profile = self._workers.get(worker_id)
        if not profile:
            return None
        profile.total_slashed += float(amount)
        profile.slash_balance += float(amount)
        profile.last_activity_at = datetime.utcnow().isoformat()
        profile.metadata.setdefault("slash_events", []).append({
            "amount": float(amount),
            "timestamp": profile.last_activity_at,
            "metadata": metadata or {},
        })
        self._workers[worker_id] = profile
        self._save()
        return profile
