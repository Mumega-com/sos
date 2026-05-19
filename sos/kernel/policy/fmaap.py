from __future__ import annotations

import json
import sqlite3
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from sos.kernel.config import DB_PATH, DEFAULT_TENANT_ID


class FMAAPPillar(str, Enum):
    FLOW = "flow"
    METABOLISM = "metabolism"
    ALIGNMENT = "alignment"
    AUTONOMY = "autonomy"
    PHYSICS = "physics"


class FMAAPValidationRequest(BaseModel):
    agent_id: str
    action: str
    resource: str
    context: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PillarResult(BaseModel):
    pillar: FMAAPPillar
    passed: bool
    score: float
    reason: str


class FMAAPValidationResponse(BaseModel):
    valid: bool
    overall_score: float
    results: List[PillarResult]
    timestamp: float = Field(default_factory=time.time)


FUEL_GRADE_COST_CENTS: dict[str, int] = {
    "diesel": 0,
    "regular": 35,
    "premium": 500,
    "aviation": 1500,
}


def _json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


class FMAAPPolicyEngine:
    """
    Validates actions against the 5 pillars of FMAAP using the live squad graph.
    """

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _lookup(mapping: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = mapping.get(key)
            if value is not None:
                return value
        return None

    def _request_scope(self, request: FMAAPValidationRequest) -> tuple[str | None, str, str | None]:
        merged = {**request.context, **request.metadata}
        squad_id = self._lookup(merged, "squad_id", "squadId")
        tenant_id = self._lookup(merged, "tenant_id", "tenantId") or DEFAULT_TENANT_ID
        skill = self._lookup(merged, "skill", "skill_id", "skillId")
        return squad_id, str(tenant_id), str(skill) if skill is not None else None

    def _estimated_cost_cents(self, request: FMAAPValidationRequest) -> tuple[int, str]:
        merged = {**request.context, **request.metadata}
        explicit = self._lookup(merged, "estimated_cost_cents", "estimatedCostCents")
        if explicit is not None:
            return max(0, int(explicit)), "explicit_estimated_cost_cents"

        explicit_units = self._lookup(merged, "estimated_cost", "estimatedCost")
        if explicit_units is not None:
            return max(0, int(explicit_units)), "explicit_estimated_cost"

        fuel_grade = str(self._lookup(merged, "fuel_grade", "fuelGrade") or "diesel").lower()
        return FUEL_GRADE_COST_CENTS.get(fuel_grade, FUEL_GRADE_COST_CENTS["diesel"]), f"fuel_grade:{fuel_grade}"

    def validate(self, request: FMAAPValidationRequest) -> FMAAPValidationResponse:
        squad_id, tenant_id, skill = self._request_scope(request)
        results: list[PillarResult] = []

        if not squad_id or not self.db_path.exists():
            results = [
                PillarResult(
                    pillar=FMAAPPillar.FLOW,
                    passed=False,
                    score=0.0,
                    reason="Missing squad context or squad database unavailable.",
                ),
                PillarResult(
                    pillar=FMAAPPillar.METABOLISM,
                    passed=False,
                    score=0.0,
                    reason="Missing squad context or squad database unavailable.",
                ),
                PillarResult(
                    pillar=FMAAPPillar.ALIGNMENT,
                    passed=False,
                    score=0.0,
                    reason="Missing squad context or squad database unavailable.",
                ),
                PillarResult(
                    pillar=FMAAPPillar.AUTONOMY,
                    passed=False,
                    score=0.0,
                    reason="Missing squad context or squad database unavailable.",
                ),
                PillarResult(
                    pillar=FMAAPPillar.PHYSICS,
                    passed=False,
                    score=0.0,
                    reason="Missing squad context or squad database unavailable.",
                ),
            ]
            return FMAAPValidationResponse(valid=False, overall_score=0.0, results=results)

        with self._connect() as conn:
            squad = conn.execute(
                "SELECT coherence, members_json, conductance_json FROM squads WHERE id = ? AND tenant_id = ?",
                (squad_id, tenant_id),
            ).fetchone()

            wallet = conn.execute(
                "SELECT balance_cents, fuel_budget_json FROM squad_wallets WHERE squad_id = ? AND tenant_id = ?",
                (squad_id, tenant_id),
            ).fetchone()

            goal = conn.execute(
                """
                SELECT coherence_threshold
                FROM squad_goals
                WHERE squad_id = ? AND tenant_id = ? AND status = 'active'
                ORDER BY coherence_threshold DESC, updated_at DESC
                LIMIT 1
                """,
                (squad_id, tenant_id),
            ).fetchone()

        if not squad:
            results = [
                PillarResult(pillar=p, passed=False, score=0.0, reason=f"Squad not found: {squad_id}")
                for p in FMAAPPillar
            ]
            return FMAAPValidationResponse(valid=False, overall_score=0.0, results=results)

        coherence = float(squad["coherence"] or 0.0)
        members = _json_loads(squad["members_json"], [])
        conductance = _json_loads(squad["conductance_json"], {})
        fuel_budget = _json_loads(wallet["fuel_budget_json"] if wallet else "{}", {})
        balance_cents = int(wallet["balance_cents"] or 0) if wallet else 0
        coherence_threshold = float(goal["coherence_threshold"]) if goal else 0.6

        # 1. Flow
        flow_passed = coherence >= 0.4
        results.append(PillarResult(
            pillar=FMAAPPillar.FLOW,
            passed=flow_passed,
            score=min(max(coherence / 0.4, 0.0), 1.0) if flow_passed else max(coherence / 0.4, 0.0),
            reason=f"Squad coherence {coherence:.2f} {'meets' if flow_passed else 'below'} flow floor 0.40.",
        ))

        # 2. Metabolism
        estimated_cost_cents, cost_source = self._estimated_cost_cents(request)
        merged = {**request.context, **request.metadata}
        fuel_grade = str(self._lookup(merged, "fuel_grade", "fuelGrade") or "diesel").lower()
        grade_budget = int(fuel_budget.get(fuel_grade, balance_cents))
        effective_budget = min(balance_cents, grade_budget)
        metabolism_passed = effective_budget >= estimated_cost_cents
        results.append(PillarResult(
            pillar=FMAAPPillar.METABOLISM,
            passed=metabolism_passed,
            score=1.0 if metabolism_passed else 0.0,
            reason=(
                f"Estimated cost {estimated_cost_cents}c ({cost_source}) "
                f"{'fits' if metabolism_passed else 'exceeds'} available {effective_budget}c "
                f"for fuel grade {fuel_grade}."
            ),
        ))

        # 3. Alignment
        if not skill:
            alignment_passed = False
            alignment_score = 0.0
            alignment_reason = "No skill supplied for conductance check."
        else:
            conductance_value = float(conductance.get(skill, 0.0))
            alignment_passed = skill in conductance
            alignment_score = max(0.0, min(conductance_value, 1.0)) if alignment_passed else 0.0
            alignment_reason = (
                f"Skill {skill} {'exists' if alignment_passed else 'missing'} in squad conductance map."
            )
        results.append(PillarResult(
            pillar=FMAAPPillar.ALIGNMENT,
            passed=alignment_passed,
            score=alignment_score,
            reason=alignment_reason,
        ))

        # 4. Autonomy
        autonomy_passed = any(member.get("agent_id") == request.agent_id for member in members)
        results.append(PillarResult(
            pillar=FMAAPPillar.AUTONOMY,
            passed=autonomy_passed,
            score=1.0 if autonomy_passed else 0.0,
            reason=f"Agent {request.agent_id} {'is' if autonomy_passed else 'is not'} a squad member.",
        ))

        # 5. Physics
        physics_passed = coherence >= coherence_threshold
        physics_score = min(coherence / coherence_threshold, 1.0) if coherence_threshold > 0 else 1.0
        results.append(PillarResult(
            pillar=FMAAPPillar.PHYSICS,
            passed=physics_passed,
            score=max(0.0, physics_score),
            reason=(
                f"Squad coherence {coherence:.2f} {'meets' if physics_passed else 'below'} "
                f"goal threshold {coherence_threshold:.2f}."
            ),
        ))

        overall_score = sum(r.score for r in results) / len(results)
        return FMAAPValidationResponse(
            valid=all(r.passed for r in results),
            overall_score=overall_score,
            results=results,
        )
