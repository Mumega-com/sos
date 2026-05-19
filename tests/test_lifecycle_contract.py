from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_cold_missing_tmux_agent_is_parked(monkeypatch):
    from sos.services.health import lifecycle

    agent_def = {
        "type": "tmux",
        "session": "demo",
        "warm_policy": "cold",
        "idle_patterns": ["$ "],
        "busy_patterns": ["Thinking"],
        "compaction_patterns": [],
    }

    monkeypatch.setattr(lifecycle, "check_tmux_alive", lambda session: False)
    monkeypatch.setattr(lifecycle, "_agent_has_active_tasks", lambda agent_id: False)
    monkeypatch.setattr(lifecycle, "_parked_override", lambda agent_id: None)

    detected = lifecycle.detect_agent_state("demo", agent_def)
    assert detected["state"] == "parked"


def test_cold_missing_tmux_agent_with_active_task_is_dead(monkeypatch):
    from sos.services.health import lifecycle

    agent_def = {
        "type": "tmux",
        "session": "demo",
        "warm_policy": "cold",
        "idle_patterns": ["$ "],
        "busy_patterns": ["Thinking"],
        "compaction_patterns": [],
    }

    monkeypatch.setattr(lifecycle, "check_tmux_alive", lambda session: False)
    monkeypatch.setattr(lifecycle, "_agent_has_active_tasks", lambda agent_id: True)
    monkeypatch.setattr(lifecycle, "_parked_override", lambda agent_id: None)

    detected = lifecycle.detect_agent_state("demo", agent_def)
    assert detected["state"] == "dead"


def test_parked_override_respected(monkeypatch):
    from sos.services.health import lifecycle

    monkeypatch.setattr(
        lifecycle,
        "load_agent_state",
        lambda agent_id: {"parked": True, "parked_reason": "waiting for worktree spawn"},
    )
    detected = lifecycle._parked_override("demo")
    assert detected == "waiting for worktree spawn"


def test_stuck_threshold_default_is_120():
    from sos.services.health import lifecycle

    assert lifecycle.STUCK_THRESHOLD_MINUTES == 120
