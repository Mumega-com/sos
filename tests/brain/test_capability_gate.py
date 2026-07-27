"""S180-A — Sovereign brain colony capability gate (agent dimension).

Tests sovereign/brain.py's _agent_home_tenant resolver + _assert_agent_in_tenant
gate in isolation — no live Redis / squad service / Gemini. sovereign/ is a
separate module root (brain.py does `from kernel.config import ...`), so we add
it to sys.path here rather than relying on a conftest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SOV = Path(__file__).resolve().parents[2] / "sovereign"
if str(_SOV) not in sys.path:
    sys.path.insert(0, str(_SOV))
os.environ.setdefault("BRAIN_TENANT_SCOPE", "*")  # the live config: a GLOBAL colony brain

import pytest  # noqa: E402

import brain  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    brain._AGENT_HOME_CACHE = {}
    brain._AGENT_HOME_CACHE_TS = 0.0
    yield
    brain._AGENT_HOME_CACHE = {}
    brain._AGENT_HOME_CACHE_TS = 0.0


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


# Mirrors the squad service /agents roster shape, with TROP's registry spelling.
_ROSTER = {"agents": [
    {"name": "sol", "project": "therealmofpatterns", "role": "SPECIALIST", "type": "OPENCLAW"},
    {"name": "dandan", "project": "dentalnearyou", "role": "SPECIALIST", "type": "OPENCLAW"},
    {"name": "gaf", "project": "gaf", "role": "SPECIALIST", "type": "OPENCLAW"},
    {"name": "worker", "project": "", "role": "EXECUTOR", "type": "OPENCLAW"},
    {"name": "kasra", "project": "", "role": "EXECUTOR", "type": "TMUX"},
]}


def _patch_roster(monkeypatch, payload=_ROSTER):
    calls = {"n": 0}

    def _get(url, **kw):
        calls["n"] += 1
        return _Resp(payload)

    monkeypatch.setattr(brain.requests, "get", _get)
    return calls


# ── _agent_home_tenant ────────────────────────────────────────────────────────

def test_resolves_tenant_bound_agent(monkeypatch):
    _patch_roster(monkeypatch)
    assert brain._agent_home_tenant("sol") == "therealmofpatterns"


def test_shared_agent_returns_none(monkeypatch):
    _patch_roster(monkeypatch)
    assert brain._agent_home_tenant("worker") is None
    assert brain._agent_home_tenant("kasra") is None


def test_unknown_agent_returns_none(monkeypatch):
    _patch_roster(monkeypatch)
    assert brain._agent_home_tenant("nobody") is None


def test_case_insensitive(monkeypatch):
    _patch_roster(monkeypatch)
    assert brain._agent_home_tenant("Sol") == "therealmofpatterns"


def test_cache_hits_avoid_refetch(monkeypatch):
    calls = _patch_roster(monkeypatch)
    brain._agent_home_tenant("sol")
    brain._agent_home_tenant("dandan")
    assert calls["n"] == 1  # second resolution served from cache


def test_cold_start_failsafe_for_tenant_bound(monkeypatch):
    def _boom(url, **kw):
        raise RuntimeError("resolver down")
    monkeypatch.setattr(brain.requests, "get", _boom)
    # cold start + resolver down: known tenant-bound agents stay GATED via the
    # static failsafe; genuinely unknown/shared agents remain ungated (None).
    assert brain._agent_home_tenant("sol") == "realm-of-patterns"
    assert brain._agent_home_tenant("dandan") == "dentalnearyou"
    assert brain._agent_home_tenant("worker") is None
    assert brain._agent_home_tenant("nobody") is None


def test_stale_cache_retained_on_refresh_failure(monkeypatch):
    _patch_roster(monkeypatch)
    assert brain._agent_home_tenant("sol") == "therealmofpatterns"  # populate cache
    brain._AGENT_HOME_CACHE_TS = 0.0  # force TTL expiry

    def _boom(url, **kw):
        raise RuntimeError("resolver flapped")
    monkeypatch.setattr(brain.requests, "get", _boom)
    # stale map retained through a transient outage → still resolves (gate stays shut)
    assert brain._agent_home_tenant("sol") == "therealmofpatterns"


# ── _assert_agent_in_tenant ───────────────────────────────────────────────────

def test_gate_blocks_cross_tenant(monkeypatch):
    _patch_roster(monkeypatch)
    # the observed bleed: sol (TROP) dispatched for a Mumega goal
    with pytest.raises(ValueError, match="Capability scope violation"):
        brain._assert_agent_in_tenant("sol", "mumega")


def test_gate_allows_same_tenant_canonical(monkeypatch):
    _patch_roster(monkeypatch)
    brain._assert_agent_in_tenant("sol", "realm-of-patterns")  # no raise


def test_gate_allows_same_tenant_via_alias(monkeypatch):
    # CRITICAL: sol.home="therealmofpatterns", goal="trop" — three spellings, one
    # tenant. Must NOT falsely block legitimate same-tenant work.
    _patch_roster(monkeypatch)
    brain._assert_agent_in_tenant("sol", "trop")  # no raise


def test_gate_allows_shared_agent_anywhere(monkeypatch):
    _patch_roster(monkeypatch)
    brain._assert_agent_in_tenant("worker", "mumega")  # no raise
    brain._assert_agent_in_tenant("kasra", "viamar")   # no raise


def test_gate_allows_unknown_agent(monkeypatch):
    _patch_roster(monkeypatch)
    brain._assert_agent_in_tenant("system", "mumega")  # unknown → open, no raise


def test_dandan_blocked_outside_dnu(monkeypatch):
    _patch_roster(monkeypatch)
    with pytest.raises(ValueError, match="Capability scope violation"):
        brain._assert_agent_in_tenant("dandan", "gaf")


def test_gaf_blocked_cross_tenant(monkeypatch):
    # regression net: gaf must be gated under the live roster (catches gaf being
    # dropped from AGENTS / the /agents roster in future)
    _patch_roster(monkeypatch)
    with pytest.raises(ValueError, match="Capability scope violation"):
        brain._assert_agent_in_tenant("gaf", "mumega")
    brain._assert_agent_in_tenant("gaf", "gaf")  # own tenant — no raise


def test_dandan_allowed_in_dnu_alias(monkeypatch):
    _patch_roster(monkeypatch)
    brain._assert_agent_in_tenant("dandan", "dnu")  # dnu → dentalnearyou, no raise


def test_gate_blocks_cross_tenant_during_resolver_outage(monkeypatch):
    def _boom(url, **kw):
        raise RuntimeError("resolver down")
    monkeypatch.setattr(brain.requests, "get", _boom)
    # cold start, resolver down — sol must still be gated out of Mumega (failsafe)
    with pytest.raises(ValueError, match="Capability scope violation"):
        brain._assert_agent_in_tenant("sol", "mumega")


# ── motor_execute end-to-end (the real observed bleed) ────────────────────────

def _action(method, agent, goal="goal_mumega", details="", title="do a thing"):
    return {"method": method, "agent": agent, "goal_id": goal,
            "details": details, "action": title}


def _patch_dispatch(monkeypatch):
    """Stub the squad roster GET + capture any task POST; neutralize side checks."""
    posts = []
    monkeypatch.setattr(brain, "_agent_available", lambda a: True)
    monkeypatch.setattr(brain, "_task_exists", lambda *a, **k: False)

    def _get(url, **kw):
        return _Resp(_ROSTER)

    def _post(url, **kw):
        posts.append((url, kw.get("json", {})))
        return _Resp({"task": {"id": "brain-test"}})

    monkeypatch.setattr(brain.requests, "get", _get)
    monkeypatch.setattr(brain.requests, "post", _post)
    return posts


def test_motor_execute_blocks_cross_tenant_create_task(monkeypatch):
    # the literal observed bleed: create_task, agent=sol, goal=mumega
    posts = _patch_dispatch(monkeypatch)
    res = brain.motor_execute(_action("create_task", "sol"))
    assert res["success"] is False
    assert "Capability scope violation" in res["result"]
    assert posts == []  # nothing dispatched


def test_motor_execute_allows_same_tenant_via_alias(monkeypatch):
    # sol on a 'trop' goal (alias of realm-of-patterns / therealmofpatterns) — allowed
    posts = _patch_dispatch(monkeypatch)
    res = brain.motor_execute(_action("create_task", "sol", goal="goal_trop"))
    assert res["success"] is True
    assert len(posts) == 1


def test_motor_execute_allows_shared_agent(monkeypatch):
    posts = _patch_dispatch(monkeypatch)
    res = brain.motor_execute(_action("create_task", "kasra"))  # shared → any project
    assert res["success"] is True
    assert len(posts) == 1


def test_motor_execute_post_content_not_capability_blocked(monkeypatch):
    # post_content dispatches as colony "brain" — even with a tenant-bound agent
    # in the directive, the (now-present) gate must not block it.
    _patch_dispatch(monkeypatch)
    monkeypatch.setattr(brain, "_generate_content", lambda d: "__CONTENT_MODE_OFF__")
    res = brain.motor_execute(_action("post_content", "sol", title="post something"))
    assert res["success"] is True
    assert "Capability scope violation" not in res.get("result", "")


def test_motor_execute_blocks_cross_tenant_outreach(monkeypatch):
    # send_outreach, agent=sol, goal=mumega, no 'dent' → outreach stays project=mumega,
    # assignee=sol (fallback) → cross-tenant → blocked on the FINAL pair.
    posts = _patch_dispatch(monkeypatch)
    res = brain.motor_execute(_action("send_outreach", "sol", details="generic outreach"))
    assert res["success"] is False
    assert "Capability scope violation" in res["result"]
    assert posts == []


# ── BLOCK-4 regression (sos-205-a7c2fc44 adversarial gate) ────────────────────
# research's mumega branch used to early-return via _mupot_dispatch_task
# BEFORE _capability_block ran, skipping the colony gate entirely for
# method="research" + project="mumega" — the only branch that did.

def test_motor_execute_blocks_research_mumega_when_gate_subject_tenant_bound(monkeypatch):
    # Make "river" (research's fixed gate subject) tenant-bound to a project
    # other than mumega, then dispatch a mumega research directive. Before
    # the fix this reached _mupot_dispatch_task before the gate ever ran;
    # after the fix it must be blocked and _mupot_dispatch_task must never
    # be called.
    roster = {"agents": _ROSTER["agents"] + [
        {"name": "river", "project": "therealmofpatterns", "role": "SPECIALIST", "type": "OPENCLAW"},
    ]}
    _patch_dispatch(monkeypatch)
    _patch_roster(monkeypatch, payload=roster)
    called = {"mupot": False}

    def _spy_dispatch(*a, **k):
        called["mupot"] = True
        return {"success": True, "result": "should never run"}

    monkeypatch.setattr(brain, "_mupot_dispatch_task", _spy_dispatch)
    res = brain.motor_execute(_action("research", "kasra", goal="goal_mumega", details="look into X"))
    assert res["success"] is False
    assert "Capability scope violation" in res["result"]
    assert called["mupot"] is False  # gate must run BEFORE dispatch, not after


def test_motor_execute_research_mumega_dispatches_when_gate_allows(monkeypatch):
    # river is shared/colony (not in the roster → no home tenant) → gate
    # passes → mumega research still routes to mupot via the guarded
    # "squad-core" target, exactly like before the fix.
    _patch_dispatch(monkeypatch)
    _patch_roster(monkeypatch)
    called = {}

    def _fake_dispatch(squad_id, title, description, priority, labels):
        called["squad_id"] = squad_id
        return {"success": True, "result": f"dispatched to {squad_id}"}

    monkeypatch.setattr(brain, "_mupot_dispatch_task", _fake_dispatch)
    res = brain.motor_execute(_action("research", "kasra", goal="goal_mumega", details="look into Y"))
    assert res["success"] is True
    assert called["squad_id"] == "squad-core"


def test_motor_execute_research_non_mumega_still_gates_and_dispatches_mirror(monkeypatch):
    # Non-mumega research path is unchanged by the fix: gate on "river", then
    # Mirror dispatch, no mupot call.
    posts = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(brain, "_mupot_dispatch_task", lambda *a, **k: pytest.fail("mupot must not be called for non-mumega research"))
    res = brain.motor_execute(_action("research", "kasra", goal="goal_gaf", details="look into Z"))
    assert res["success"] is True
    assert len(posts) == 1
    assert posts[0][0] == f"{brain.MIRROR_URL}/tasks"
