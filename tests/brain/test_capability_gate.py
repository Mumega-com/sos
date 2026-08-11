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
#
# Re-gate update (sos-205-b5307dd7): the gate subject used to be the
# hardcoded literal "river", which has no home tenant in ANY roster
# (_agent_home_tenant('river') is always None) — so a test that made "river"
# tenant-bound and then dispatched with agent="kasra" was only exercising
# the OLD hardcoded subject, not the real one. The fix gates the real
# `agent` value from the action, so these tests now drive the gate through
# that same real subject — including a genuinely tenant-bound agent (digid)
# to prove the gate can still deny.

def test_motor_execute_blocks_research_mumega_when_gate_subject_tenant_bound(monkeypatch):
    # digid is tenant-bound to project "digid" (non-None home tenant).
    # Dispatch a mumega research directive AS digid: this must be blocked,
    # and _mupot_dispatch_task must never be called.
    roster = {"agents": _ROSTER["agents"] + [
        {"name": "digid", "project": "digid", "role": "SPECIALIST", "type": "OPENCLAW"},
    ]}
    _patch_dispatch(monkeypatch)
    _patch_roster(monkeypatch, payload=roster)
    called = {"mupot": False}

    def _spy_dispatch(*a, **k):
        called["mupot"] = True
        return {"success": True, "result": "should never run"}

    monkeypatch.setattr(brain, "_mupot_dispatch_task", _spy_dispatch)
    res = brain.motor_execute(_action("research", "digid", goal="goal_mumega", details="look into X"))
    assert res["success"] is False
    assert "Capability scope violation" in res["result"]
    assert called["mupot"] is False  # gate must run BEFORE dispatch, not after


def test_motor_execute_research_mumega_dispatches_when_gate_allows(monkeypatch):
    # kasra is shared/colony (project="" in the roster → no home tenant) →
    # gate passes on the real acting agent → mumega research still routes to
    # mupot via the guarded "squad-core" target, exactly like before the fix.
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
    # Non-mumega research path is unchanged by the fix: gate on the real
    # acting agent (kasra, shared → no home tenant), then Mirror dispatch,
    # no mupot call.
    posts = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(brain, "_mupot_dispatch_task", lambda *a, **k: pytest.fail("mupot must not be called for non-mumega research"))
    res = brain.motor_execute(_action("research", "kasra", goal="goal_gaf", details="look into Z"))
    assert res["success"] is True
    assert len(posts) == 1
    assert posts[0][0] == f"{brain.MIRROR_URL}/tasks"


def test_motor_execute_blocks_research_non_mumega_cross_tenant(monkeypatch):
    # New (sos-205-b5307dd7): the real-subject gate must also fire on the
    # non-mumega dispatch path, not just mumega — digid dispatched for a
    # DIFFERENT tenant's goal must be blocked before any Mirror POST.
    # Project deliberately has NO PROJECT_LEADS entry so `agent` is not
    # rerouted before the gate runs (gaf/dentalnearyou/etc. would silently
    # swap "digid" for their own lead, which is a routing property, not part
    # of what this test is checking).
    posts = _patch_dispatch(monkeypatch)
    roster = {"agents": _ROSTER["agents"] + [
        {"name": "digid", "project": "digid", "role": "SPECIALIST", "type": "OPENCLAW"},
    ]}
    _patch_roster(monkeypatch, payload=roster)
    monkeypatch.setattr(brain, "_mupot_dispatch_task", lambda *a, **k: pytest.fail("mupot must not be called for this project"))
    res = brain.motor_execute(_action("research", "digid", goal="goal_unknown-tenant", details="look into W"))
    assert res["success"] is False
    assert "Capability scope violation" in res["result"]
    assert posts == []


# ── P2-D: agent-subject normalization (sos-205-47f5f8c2 gate-3) ────────────
# `_agent_home_tenant` used to key its roster lookup with a bare
# `str(agent).strip().lower()`. A zero-width space, a trailing dot/slash, an
# embedded space, or a non-str value (None/list/dict) each turned a
# roster-miss into "no home tenant = ungated colony agent". The fix
# normalizes (NFKC + zero-width strip + casefold) once, at the top of
# motor_execute, and rejects non-str/empty subjects outright instead of
# coercing them with str(...). The roster default-deny in _agent_available
# remains the enforcing layer for actual dispatchability — these tests
# exercise it with it bypassed (_patch_dispatch), matching how gate-3 proved
# the underlying mutation class, and separately confirm motor_execute's own
# entry-point guard for the non-str cases.


def test_normalize_agent_subject_strips_zero_width_and_casefolds():
    assert brain._normalize_agent_subject("Digid") == "digid"
    assert brain._normalize_agent_subject(" digid ") == "digid"
    assert brain._normalize_agent_subject("digid​") == "digid"  # zero-width space
    assert brain._normalize_agent_subject("digid﻿") == "digid"  # BOM / ZWNBSP


def test_normalize_agent_subject_rejects_non_str_and_empty():
    assert brain._normalize_agent_subject(None) is None
    assert brain._normalize_agent_subject(0) is None
    assert brain._normalize_agent_subject(["digid"]) is None
    assert brain._normalize_agent_subject({"a": "digid"}) is None
    assert brain._normalize_agent_subject("") is None
    assert brain._normalize_agent_subject("   ") is None


def test_normalize_agent_subject_homoglyph_does_not_crash_and_does_not_match():
    # NFKC does not fold the Turkish dotless-i to 'i' (distinct codepoint,
    # not a canonical equivalence) — this is documented, not a bypass: the
    # normalized string simply doesn't match the roster, same as any other
    # honestly-unknown agent name, and _agent_available's exact-match
    # whitelist is what actually decides dispatchability.
    assert brain._normalize_agent_subject("dıgıd") == "dıgıd"


# ── WARN-I / LOW-J: symmetric roster normalization (sos-205-790a2a63 gate-4)
# The lookup side (`_normalize_agent_subject`) normalized NFKC + zero-width +
# casefold, but the roster SIDES did not: `_AGENT_HOME_CACHE` was built with
# a bare `.strip().lower()`, and `_AGENT_SESSION` (the actual dispatch
# whitelist `_agent_available` enforces) was compared against with plain `in`
# on un-normalized literals. Normalizing only the lookup side is worse than
# normalizing neither: it makes non-normal roster ENTRIES unreachable (LOW-J
# — resolves to home=None, i.e. ungated) while ALSO silently widening the
# exact-match whitelist to accept mutated spellings (WARN-I). Fix: run BOTH
# sides through the SAME `_normalize_agent_subject` pipeline. The widening is
# now deliberate and documented, not an accident.


def test_agent_home_tenant_roster_key_reachable_when_registry_name_has_zero_width(monkeypatch):
    # LOW-J: a roster row whose `name` is not already NFKC-normal (here, a
    # zero-width space embedded in the registered name) used to build an
    # UNREACHABLE map key — a lookup for the plain, canonical spelling
    # resolved home=None (ungated colony agent), the wrong direction for a
    # tenant-bound name.
    roster = {"agents": [
        {"name": "so​l", "project": "therealmofpatterns", "role": "SPECIALIST", "type": "OPENCLAW"},
    ]}
    _patch_roster(monkeypatch, payload=roster)
    assert brain._agent_home_tenant("sol") == "therealmofpatterns"


def test_agent_home_tenant_roster_key_fullwidth_reachable(monkeypatch):
    roster = {"agents": [
        {"name": "ｓｏｌ", "project": "therealmofpatterns", "role": "SPECIALIST", "type": "OPENCLAW"},
    ]}
    _patch_roster(monkeypatch, payload=roster)
    assert brain._agent_home_tenant("sol") == "therealmofpatterns"


def test_agent_available_accepts_case_and_zero_width_mutations_of_roster_entry():
    # 'system' has an empty tmux-session requirement (session == ""), so
    # this exercises the roster-membership check in isolation without a live
    # tmux dependency. 'SYSTEM'/fullwidth/zero-width-padded spellings are now
    # a DELIBERATE, documented accept (WARN-I) — the same identity as
    # 'system', not a different one silently let through.
    assert brain._agent_available("system") is True
    assert brain._agent_available("SYSTEM") is True
    assert brain._agent_available("system​") is True  # zero-width space
    assert brain._agent_available("ｓｙｓｔｅｍ") is True  # fullwidth


def test_agent_available_rejects_unknown_agent():
    assert brain._agent_available("nobody") is False
    assert brain._agent_available("") is False


def test_motor_execute_rejects_non_str_agent_with_calm_skip(monkeypatch):
    # Unhashable values (list/dict) used to reach `agent not in
    # _AGENT_SESSION` (a dict membership test) and raise an uncaught
    # TypeError there, crashing the whole brain cycle. Must now be a calm,
    # non-error skip instead.
    for bad_agent in (None, ["digid"], {"a": "digid"}, 0):
        res = brain.motor_execute(_action("create_task", bad_agent))
        assert res["success"] is True
        assert res.get("skipped") is True
        assert "invalid agent subject" in res["result"]


def test_motor_execute_gate_now_catches_case_and_zero_width_mutations(monkeypatch):
    # These normalize to the exact roster key ("digid") under NFKC + strip +
    # casefold — pre-fix they missed the roster and slipped through
    # ungated; post-fix the gate catches them like the canonical name.
    roster = {"agents": _ROSTER["agents"] + [
        {"name": "digid", "project": "digid", "role": "SPECIALIST", "type": "OPENCLAW"},
    ]}
    posts = _patch_dispatch(monkeypatch)
    _patch_roster(monkeypatch, payload=roster)

    for mutated in ("digid​", "DIGID", " digid ", "digid﻿"):
        posts.clear()
        res = brain.motor_execute(_action("create_task", mutated, goal="goal_mumega"))
        assert res["success"] is False, f"{mutated!r} should be caught by the gate post-normalization"
        assert "Capability scope violation" in res["result"]
        assert posts == []


def test_motor_execute_gate_evasion_strings_refuse_cleanly_not_crash(monkeypatch):
    # These do NOT normalize to a roster hit (punctuation/embedded-space/NUL
    # mutations aren't Unicode-equivalence, and normalization deliberately
    # doesn't try to collapse them — see _normalize_agent_subject's
    # docstring). They fall back to "unknown agent" — the SAME safe,
    # documented outcome any honestly-unrecognized name gets
    # (test_gate_allows_unknown_agent). The property under test is "does not
    # crash and does not silently act as a DIFFERENT, tenant-bound identity"
    # — not "gets denied", which is not this fix's job with
    # _agent_available bypassed.
    roster = {"agents": _ROSTER["agents"] + [
        {"name": "digid", "project": "digid", "role": "SPECIALIST", "type": "OPENCLAW"},
    ]}
    posts = _patch_dispatch(monkeypatch)
    _patch_roster(monkeypatch, payload=roster)

    for mutated in ("digid.", "digid/", "digid\x00", "di gid"):
        posts.clear()
        res = brain.motor_execute(_action("create_task", mutated, goal="goal_mumega"))
        assert isinstance(res, dict)
        assert "success" in res
        assert "Capability scope violation" not in res.get("result", "")
