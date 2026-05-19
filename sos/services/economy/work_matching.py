"""
Work Matching
=============
Simple capability-based matching between work units and workers.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .work_ledger import WorkLedger, WorkUnit, WorkUnitStatus, DisputeRecord
from .worker_registry import WorkerRegistry, WorkerProfile


def _normalize_list(values: Optional[List[str]]) -> List[str]:
    return [value.strip().lower() for value in (values or []) if value and value.strip()]


def score_match(unit: WorkUnit, worker: WorkerProfile) -> float:
    """
    Score a worker's fit for a work unit.
    
    Formula:
    - Base: Capability overlap (+1.0 per match)
    - Role: Role overlap (+0.5 per match)
    - Reputation: +2.0 * reputation_score (0.0-1.0)
    - Tier Bonus:
        - MASTER: +0.5
        - EXPERT: +0.3
        - JOURNEYMAN: +0.1
    - Filter: If worker matches 0 capabilities provided in 'required_capabilities', score = 0
    """
    required_caps = _normalize_list(unit.metadata.get("capabilities", [])) # Recommended
    mandatory_caps = _normalize_list(unit.metadata.get("required_capabilities", [])) # Strict
    required_roles = _normalize_list(unit.metadata.get("roles", []))
    
    worker_caps = _normalize_list(worker.capabilities)
    worker_roles = _normalize_list(worker.roles)

    # 1. Strict Filtering
    if mandatory_caps:
        missing = set(mandatory_caps) - set(worker_caps)
        if missing:
            return 0.0

    # 2. Overlap Scoring
    cap_overlap = len(set(required_caps) & set(worker_caps))
    role_overlap = len(set(required_roles) & set(worker_roles))

    score = cap_overlap * 1.0 + role_overlap * 0.5
    
    # 3. Reputation Weighting
    score += worker.reputation_score * 2.0
    
    # 4. Tier Bonus
    # We recalculate locally to avoid depending on TrustGate lookup, purely performance based
    try:
        tier = worker._calculate_tier()
        if tier == "MASTER":
            score += 0.5
        elif tier == "EXPERT":
            score += 0.3
        elif tier == "JOURNEYMAN":
            score += 0.1
    except AttributeError:
        pass # Should not happen if WorkerProfile is correct

    # Normalization for small lists (legacy support)
    if required_caps:
        score += cap_overlap / max(len(required_caps), 1)
    
    return round(score, 3)


def match_work_for_worker(
    worker_id: str,
    ledger: Optional[WorkLedger] = None,
    registry: Optional[WorkerRegistry] = None,
    limit: int = 10,
) -> List[Tuple[WorkUnit, float]]:
    ledger = ledger or WorkLedger()
    registry = registry or WorkerRegistry()
    worker = registry.get_worker(worker_id)
    if not worker:
        return []

    work_units = ledger.list_work_units(status=WorkUnitStatus.QUEUED)
    scored = [(unit, score_match(unit, worker)) for unit in work_units]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


def match_workers_for_work(
    work_id: str,
    ledger: Optional[WorkLedger] = None,
    registry: Optional[WorkerRegistry] = None,
    limit: int = 10,
) -> List[Tuple[WorkerProfile, float]]:
    ledger = ledger or WorkLedger()
    registry = registry or WorkerRegistry()
    unit = ledger.get_work_unit(work_id)
    if not unit:
        return []

    workers = registry.list_workers()
    scored = [(worker, score_match(unit, worker)) for worker in workers]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


def score_arbiter(dispute: DisputeRecord, worker: WorkerProfile, work_unit: Optional[WorkUnit] = None) -> float:
    """
    Score an arbiter candidate for a dispute.
    
    Formula:
    - Must have role "arbiter"
    - Exclude if worker is directly involved (work worker_id or challenger_id)
    - Reputation: +2.0 * reputation_score
    - Experience: log(total_verified + 1) / 5
    - Dispute penalty: -1.0 * dispute_rate
    """
    roles = _normalize_list(worker.roles)
    if "arbiter" not in roles:
        return 0.0
    if work_unit and worker.worker_id == work_unit.worker_id:
        return 0.0
    if worker.worker_id == dispute.challenger_id:
        return 0.0

    import math

    score = worker.reputation_score * 2.0
    score += math.log(worker.total_verified + 1) / 5
    score -= worker.dispute_rate * 1.0
    return round(max(0.0, score), 3)


def match_arbiters_for_dispute(
    dispute_id: str,
    ledger: Optional[WorkLedger] = None,
    registry: Optional[WorkerRegistry] = None,
    limit: int = 10,
) -> List[Tuple[WorkerProfile, float]]:
    ledger = ledger or WorkLedger()
    registry = registry or WorkerRegistry()
    dispute = ledger.get_dispute(dispute_id)
    if not dispute:
        return []

    work_unit = ledger.get_work_unit(dispute.work_id) if dispute.work_id else None
    workers = registry.list_workers()
    scored = [(worker, score_arbiter(dispute, worker, work_unit)) for worker in workers]
    scored = [pair for pair in scored if pair[1] > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]
