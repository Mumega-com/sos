from __future__ import annotations

import json


def _seed_squad_db(db_path):
    from sos.services.squad import service as squad_service

    class _RedisStub:
        def publish(self, *args, **kwargs):
            return 1

        def xadd(self, *args, **kwargs):
            return "1-0"

    squad_service.redis.Redis = lambda **kwargs: _RedisStub()
    db = squad_service.SquadDB(db_path)
    return db


def test_fmaap_passes_with_valid_squad_state(tmp_path, monkeypatch):
    from sos.kernel.policy.fmaap import FMAAPPolicyEngine, FMAAPValidationRequest

    db = _seed_squad_db(tmp_path / "squads.db")

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO squads (
                id, tenant_id, name, project, objective, tier, status,
                roles_json, members_json, kpis_json, budget_cents_monthly,
                created_at, updated_at, dna_vector, coherence, receptivity, conductance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sq-1", "default", "Glass", "inkwell", "Deliver value", "nomad", "active",
                "[]",
                json.dumps([{"agent_id": "codex", "role": "builder"}]),
                "[]",
                10000,
                "2026-04-15T00:00:00+00:00",
                "2026-04-15T00:00:00+00:00",
                "[]",
                0.72,
                0.5,
                json.dumps({"publishing": 0.9}),
            ),
        )
        conn.execute(
            """
            INSERT INTO squad_wallets (
                squad_id, tenant_id, balance_cents, total_earned_cents, total_spent_cents, fuel_budget_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sq-1", "default", 1000, 0, 0,
                json.dumps({"premium": 1000}),
                "2026-04-15T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO squad_goals (
                id, squad_id, tenant_id, target, markers_json, coherence_threshold, deadline, status, progress, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goal-1", "sq-1", "default", "Ship Glass",
                "[]", 0.6, None, "active", 0.0,
                "2026-04-15T00:00:00+00:00", "2026-04-15T00:00:00+00:00",
            ),
        )

    engine = FMAAPPolicyEngine(db.db_path)
    response = engine.validate(FMAAPValidationRequest(
        agent_id="codex",
        action="publish",
        resource="/api/publishing/library",
        metadata={
            "squad_id": "sq-1",
            "tenant_id": "default",
            "skill": "publishing",
            "fuel_grade": "premium",
        },
    ))

    assert response.valid is True
    assert all(result.passed for result in response.results)


def test_fmaap_blocks_missing_alignment_and_membership(tmp_path):
    from sos.kernel.policy.fmaap import FMAAPPolicyEngine, FMAAPValidationRequest, FMAAPPillar

    db = _seed_squad_db(tmp_path / "squads.db")

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO squads (
                id, tenant_id, name, project, objective, tier, status,
                roles_json, members_json, kpis_json, budget_cents_monthly,
                created_at, updated_at, dna_vector, coherence, receptivity, conductance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sq-2", "default", "Clockwork", "sos", "Guard engine", "construct", "active",
                "[]",
                json.dumps([{"agent_id": "kasra", "role": "architect"}]),
                "[]",
                10000,
                "2026-04-15T00:00:00+00:00",
                "2026-04-15T00:00:00+00:00",
                "[]",
                0.8,
                0.5,
                json.dumps({"routing": 0.7}),
            ),
        )
        conn.execute(
            """
            INSERT INTO squad_wallets (
                squad_id, tenant_id, balance_cents, total_earned_cents, total_spent_cents, fuel_budget_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sq-2", "default", 1000, 0, 0,
                json.dumps({"diesel": 1000}),
                "2026-04-15T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO squad_goals (
                id, squad_id, tenant_id, target, markers_json, coherence_threshold, deadline, status, progress, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goal-2", "sq-2", "default", "Guard FMAAP",
                "[]", 0.7, None, "active", 0.0,
                "2026-04-15T00:00:00+00:00", "2026-04-15T00:00:00+00:00",
            ),
        )

    engine = FMAAPPolicyEngine(db.db_path)
    response = engine.validate(FMAAPValidationRequest(
        agent_id="codex",
        action="api_request",
        resource="/engine/run",
        metadata={
            "squad_id": "sq-2",
            "tenant_id": "default",
            "skill": "publishing",
            "fuel_grade": "diesel",
        },
    ))

    assert response.valid is False
    by_pillar = {result.pillar: result for result in response.results}
    assert by_pillar[FMAAPPillar.ALIGNMENT].passed is False
    assert by_pillar[FMAAPPillar.AUTONOMY].passed is False


def test_fmaap_blocks_when_wallet_or_coherence_is_too_low(tmp_path):
    from sos.kernel.policy.fmaap import FMAAPPolicyEngine, FMAAPValidationRequest, FMAAPPillar

    db = _seed_squad_db(tmp_path / "squads.db")

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO squads (
                id, tenant_id, name, project, objective, tier, status,
                roles_json, members_json, kpis_json, budget_cents_monthly,
                created_at, updated_at, dna_vector, coherence, receptivity, conductance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sq-3", "default", "Budget", "sos", "Stay solvent", "nomad", "active",
                "[]",
                json.dumps([{"agent_id": "codex", "role": "builder"}]),
                "[]",
                10000,
                "2026-04-15T00:00:00+00:00",
                "2026-04-15T00:00:00+00:00",
                "[]",
                0.35,
                0.5,
                json.dumps({"publishing": 0.9}),
            ),
        )
        conn.execute(
            """
            INSERT INTO squad_wallets (
                squad_id, tenant_id, balance_cents, total_earned_cents, total_spent_cents, fuel_budget_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sq-3", "default", 40, 0, 0,
                json.dumps({"premium": 40}),
                "2026-04-15T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO squad_goals (
                id, squad_id, tenant_id, target, markers_json, coherence_threshold, deadline, status, progress, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goal-3", "sq-3", "default", "Ship safely",
                "[]", 0.6, None, "active", 0.0,
                "2026-04-15T00:00:00+00:00", "2026-04-15T00:00:00+00:00",
            ),
        )

    engine = FMAAPPolicyEngine(db.db_path)
    response = engine.validate(FMAAPValidationRequest(
        agent_id="codex",
        action="publish",
        resource="/api/publishing/access/chapter-1",
        metadata={
            "squad_id": "sq-3",
            "tenant_id": "default",
            "skill": "publishing",
            "fuel_grade": "premium",
        },
    ))

    by_pillar = {result.pillar: result for result in response.results}
    assert response.valid is False
    assert by_pillar[FMAAPPillar.FLOW].passed is False
    assert by_pillar[FMAAPPillar.METABOLISM].passed is False
    assert by_pillar[FMAAPPillar.PHYSICS].passed is False
