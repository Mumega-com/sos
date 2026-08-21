"""Sovereign brain — the decide-time roster filter (phantom dispatch).

WHAT BROKE (observed live, 2026-08-13 20:00–20:33 UTC)

The decision prompt hardcoded its roster:

    "agent": "which agent should do it (kasra/athena/sol/dandan/system)"

`sol` has had no tmux session for some time. The only availability check lived
in ``motor_execute``, i.e. AFTER the model had already chosen. So a cycle would
perceive, think, decide, write a receipt and emit a bus message naming 'sol' —
and only then throw the work away with "Agent 'sol' unavailable". Six of eight
consecutive cycles produced nothing but skip notices.

Then it got worse in the way this class of bug always does: the brain started
proposing ``admin_shell`` actions to restart 'sol', which failed as an
unsupported method, after which it began *researching why admin_shell is
unsupported*. A filter in the wrong place stopped being noise and became a loop
generating work about itself. Nothing downstream can fix that — by the time the
executor sees the action, the receipt has already been emitted.

WHAT THESE TESTS PIN

The prompt is the artifact that governs which agent gets chosen, so that is what
is asserted here — not the helper in isolation. A test that only checked
``_dispatchable_agents()`` would pass while the prompt still advertised 'sol'.

Both doors keep their check (``_dispatchable_agents`` at decide time,
``_agent_available`` in ``motor_execute``), and both call ONE predicate. The
last test pins that, because the recurring failure in this codebase is two
copies of a predicate where only one gets fixed.
"""
from __future__ import annotations

import re
import sys
import types

import pytest

import sovereign.brain as brain


def names(prompt: str) -> set[str]:
    """Agent names mentioned in *prompt*, matched on word boundaries.

    Naive `"sol" in prompt` is a false positive: 'sol' is a substring of
    "resolve", which appears in the standing rules. The first draft of this file
    failed for exactly that reason, on a prompt that was already correct.
    """
    return set(re.findall(r"[a-z][a-z0-9_-]*", prompt.lower()))


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_fleet(monkeypatch):
    """A fleet where 'kasra' and 'river' are up, 'sol' and 'dandan' are down."""
    sessions = {
        "kasra": "kasra",
        "river": "river",
        "sol": "sol",
        "dandan": "dandan",
        "system": "",
    }
    live = {"kasra", "river"}
    monkeypatch.setattr(brain, "_AGENT_SESSION", sessions)
    monkeypatch.setattr(
        brain, "_agent_available",
        lambda agent: not sessions.get(agent, "") or sessions[agent] in live,
    )
    return sessions


def capture_prompt(monkeypatch) -> list[str]:
    """Install a fake google.genai that records the prompt instead of calling it."""
    seen: list[str] = []

    class _Models:
        def generate_content(self, model=None, contents=None, config=None):
            seen.append(contents)
            resp = types.SimpleNamespace(
                text='{"action":"x","goal_id":"maintenance","agent":"system",'
                     '"method":"health_check","details":"d",'
                     '"expected_progress":0.1,"risk":0.1}',
                usage_metadata=types.SimpleNamespace(total_token_count=10),
            )
            return resp

    class _Client:
        def __init__(self, **kwargs):
            self.models = _Models()

    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = _Client
    google_mod = types.ModuleType("google")
    google_mod.genai = genai_mod
    cache_mod = types.ModuleType("kernel.brain_cache")
    cache_mod.get_cache_name = lambda: None  # non-cached path

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "kernel.brain_cache", cache_mod)
    monkeypatch.setattr(brain, "_budget_exhausted", lambda: False)
    return seen


# ── the roster helper ────────────────────────────────────────────────────────

def test_offline_agents_are_not_dispatchable(fake_fleet):
    roster = brain._dispatchable_agents()
    assert "sol" not in roster
    assert "dandan" not in roster
    assert "kasra" in roster and "river" in roster


def test_system_is_always_dispatchable(monkeypatch):
    """The roster can never be empty, or the brain would have nowhere to send
    anything and every cycle would deadlock. 'system' needs no session."""
    monkeypatch.setattr(brain, "_agent_available", lambda a: a == "system")
    assert brain._dispatchable_agents() == ["system"]


# ── the prompt: the artifact that actually governs the choice ────────────────

def test_decision_prompt_does_not_offer_an_offline_agent(fake_fleet, monkeypatch):
    """THE regression test. Before the fix the literal string 'sol' appeared in
    the prompt twice — in the rules line and in the JSON schema hint — so the
    model was being told, every cycle, that a dead agent was a valid choice."""
    seen = capture_prompt(monkeypatch)
    brain.prefrontal_think("delta: nothing notable")
    assert seen, "prompt was never sent"
    mentioned = names(seen[0])
    assert "sol" not in mentioned
    assert "dandan" not in mentioned


def test_decision_prompt_lists_the_agents_that_are_up(fake_fleet, monkeypatch):
    seen = capture_prompt(monkeypatch)
    brain.prefrontal_think("delta: nothing notable")
    prompt = seen[0]
    assert "kasra" in prompt and "river" in prompt
    assert "ONLINE AGENTS RIGHT NOW" in prompt


def test_prompt_forbids_reviving_offline_agents(fake_fleet, monkeypatch):
    """The escalation was the brain trying to restart 'sol' itself. Agent
    lifecycle is not the brain's job; ranking is."""
    seen = capture_prompt(monkeypatch)
    brain.prefrontal_think("delta: nothing notable")
    assert "do NOT propose bringing one back online" in seen[0]


# ── the fallback path: the hole a partial fix would leave ────────────────────

def test_fallback_prompts_carry_the_same_roster_constraint(fake_fleet, monkeypatch):
    """fallback_think runs when the token budget is exhausted — the BUSY cycles.
    Its two prompts named no roster at all, so fixing only prefrontal_think
    would have left the phantom dispatch alive on exactly the cycles that matter
    most. Asserted via the Vertex fallback, reached by disabling GitHub Models."""
    seen: list[str] = []

    class _Models:
        def generate_content(self, model=None, contents=None, config=None):
            seen.append(contents)
            return types.SimpleNamespace(text="{}", usage_metadata=None)

    class _Client:
        def __init__(self, **kwargs):
            self.models = _Models()

    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = _Client
    google_mod = types.ModuleType("google")
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setattr(brain, "GITHUB_TOKEN", "")  # force the Vertex fallback

    brain.fallback_think("delta")
    assert seen, "fallback prompt was never sent"
    assert "sol" not in names(seen[0])
    assert "MUST be one of" in seen[0]


# ── one predicate, two doors ─────────────────────────────────────────────────

def test_both_doors_read_the_same_predicate(fake_fleet, monkeypatch):
    """motor_execute keeps its own check (an agent can die between decide and
    execute). What must NOT happen is the two doors disagreeing. Both call
    _agent_available, so patching it moves both — if this test fails, someone
    has reintroduced a second copy of the liveness rule."""
    calls: list[str] = []

    def spy(agent: str) -> bool:
        calls.append(agent)
        return agent in ("kasra", "system")

    monkeypatch.setattr(brain, "_agent_available", spy)

    assert brain._dispatchable_agents() == ["kasra", "system"]
    calls.clear()

    result = brain.motor_execute(
        {"method": "health_check", "agent": "sol", "action": "t", "details": "d"}
    )
    assert "sol" in calls, "motor_execute stopped consulting _agent_available"
    assert "unavailable" in result["result"] or "paused" in result["result"] or "not dispatched" in result["result"]
