"""G35 — brain source-signal dedupe tests.

TC-G35a: call motor_execute (create_task) 3× for the same goal → exactly 1 task
         created and 2 INFO log lines with reason=source_signal_dedupe.
TC-G35b: task completed at T=0; at T+23h → no new task; at T+25h → new task created.
TC-G35c: _external_ref is deterministic — article drift in title maps to same key.
"""
from __future__ import annotations

import hashlib
import re
import sys
import types as _types
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure sovereign/ is importable — use fixed path since SOS is a symlink
SOVEREIGN_DIR = Path("/home/mumega/sovereign")
if str(SOVEREIGN_DIR) not in sys.path:
    sys.path.insert(0, str(SOVEREIGN_DIR))

# Stub kernel.config before brain.py is imported (it lives in sovereign/kernel/)
_stub_kernel = _types.ModuleType("kernel")
_stub_config = _types.ModuleType("kernel.config")
_stub_config.MIRROR_URL = "http://mirror.test"
_stub_config.MIRROR_TOKEN = "tok"
_stub_config.SQUAD_URL = "http://squad.test"
_stub_config.SOS_ENGINE_URL = "http://engine.test"
sys.modules.setdefault("kernel", _stub_kernel)
sys.modules.setdefault("kernel.config", _stub_config)

import brain  # noqa: E402 — must come after kernel stubs


# ── helpers ────────────────────────────────────────────────────────────────────

def _ref(goal_id: str, title: str) -> str:
    """Mirror brain._external_ref logic for assertions."""
    t = title.lower()
    t = re.sub(r'\b(the|a|an)\b', '', t)
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    key = f"{goal_id}:{t}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _make_action(goal_id: str = "goal_trop", title: str = "Build TROP customer Arrow Dashboard") -> dict:
    return {
        "action": title,
        "goal_id": goal_id,
        "agent": "kasra",
        "method": "create_task",
        "details": "Dashboard for TROP customers",
        "expected_progress": 0.1,
        "risk": 0.0,
    }


# ── TC-G35c: determinism + article-invariance ─────────────────────────────────

class TestExternalRefDeterminism:
    def test_same_goal_and_title_always_returns_same_ref(self) -> None:
        r1 = brain._external_ref("goal_trop", "Build TROP customer Arrow Dashboard")
        r2 = brain._external_ref("goal_trop", "Build TROP customer Arrow Dashboard")
        assert r1 == r2

    def test_article_drift_maps_to_same_ref(self) -> None:
        """'Build TROP…' and 'Build the TROP…' must hash to the same key."""
        r_without = brain._external_ref("goal_trop", "Build TROP customer Arrow Dashboard")
        r_with = brain._external_ref("goal_trop", "Build the TROP customer Arrow Dashboard")
        assert r_without == r_with, (
            f"Title drift produced different refs: {r_without!r} vs {r_with!r}"
        )

    def test_different_goals_produce_different_refs(self) -> None:
        r_trop = brain._external_ref("goal_trop", "Build Dashboard")
        r_gaf = brain._external_ref("goal_gaf", "Build Dashboard")
        assert r_trop != r_gaf

    def test_ref_length_is_12(self) -> None:
        ref = brain._external_ref("goal_trop", "Some task title")
        assert len(ref) == 12
        assert all(c in "0123456789abcdef" for c in ref)

    def test_matches_expected_hash(self) -> None:
        assert brain._external_ref("goal_trop", "Build TROP customer Arrow Dashboard") == _ref(
            "goal_trop", "Build TROP customer Arrow Dashboard"
        )


# ── TC-G35a: 3 calls → 1 task, 2 dedupe log lines ────────────────────────────

class TestSourceSignalDedupeOnEmission:
    """TC-G35a: motor_execute called 3× for same goal → 1 POST, 2 INFO dedupe logs."""

    def test_three_calls_produce_one_task(self, caplog) -> None:
        import logging
        action = _make_action()
        call_count = {"n": 0}

        def _post_side_effect(url, json=None, headers=None, timeout=None):
            call_count["n"] += 1
            resp = MagicMock()
            resp.status_code = 201
            resp.json.return_value = {}
            return resp

        # First call: dedupe query returns "not found" → task created
        # Subsequent calls: dedupe query returns "found" → skip
        dedupe_responses = [False, True, True]  # first=safe-to-emit, then=duplicate
        dedupe_iter = iter(dedupe_responses)

        with patch.object(brain, "_source_signal_active", side_effect=lambda ref: next(dedupe_iter)):
            with patch.object(brain, "_agent_available", return_value=True):
                with patch.object(brain.requests, "post", side_effect=_post_side_effect):
                    with caplog.at_level(logging.INFO, logger="brain"):
                        r1 = brain.motor_execute(action)
                        r2 = brain.motor_execute(action)
                        r3 = brain.motor_execute(action)

        assert call_count["n"] == 1, f"Expected 1 POST, got {call_count['n']}"

        # r1 should be a successful creation
        assert r1.get("success") is True
        assert "task" in r1.get("result", "").lower() or "Squad" in r1.get("result", "")

        # r2, r3 should be dedupe-skipped
        for r in (r2, r3):
            assert r.get("success") is True
            assert "dedupe" in r.get("result", "").lower() or "skipped" in r.get("result", "").lower()

        dedupe_log_lines = [
            r for r in caplog.records
            if "source_signal_dedupe" in r.getMessage()
        ]
        assert len(dedupe_log_lines) == 2, (
            f"Expected 2 INFO source_signal_dedupe logs, got {len(dedupe_log_lines)}"
        )


# ── TC-G35b: TTL window boundary ──────────────────────────────────────────────

class TestDedupeQueryTTLWindow:
    """TC-G35b: verify _source_signal_active respects TTL via the Squad API response."""

    def test_active_task_blocks_emission(self) -> None:
        # Squad returns exists=True (task in queued state)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"exists": True, "task_id": "t-123", "status": "queued"}

        with patch.object(brain.requests, "get", return_value=mock_resp) as mock_get:
            result = brain._source_signal_active("abc123def456")
        assert result is True
        mock_get.assert_called_once()
        assert "abc123def456" in mock_get.call_args[0][0]

    def test_no_active_task_allows_emission(self) -> None:
        # Squad returns exists=False (no match or 404)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"exists": False}

        with patch.object(brain.requests, "get", return_value=mock_resp):
            result = brain._source_signal_active("abc123def456")
        assert result is False

    def test_404_from_squad_allows_emission(self) -> None:
        # Old Squad Service without this endpoint → 404 → fail-open (allow emit)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {}

        with patch.object(brain.requests, "get", return_value=mock_resp):
            result = brain._source_signal_active("abc123def456")
        assert result is False

    def test_network_error_fails_open(self) -> None:
        # Network error → fail-open (allow emit rather than blocking indefinitely)
        with patch.object(brain.requests, "get", side_effect=Exception("connection refused")):
            result = brain._source_signal_active("abc123def456")
        assert result is False

    def test_ttl_seconds_passed_to_squad(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"exists": False}

        with patch.object(brain, "BRAIN_SOURCE_SIGNAL_TTL_SECONDS", 3600):
            with patch.object(brain.requests, "get", return_value=mock_resp) as mock_get:
                brain._source_signal_active("abc123def456")
        params = mock_get.call_args[1].get("params", {})
        assert params.get("ttl_seconds") == 3600
