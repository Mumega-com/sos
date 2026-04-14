"""Tests for the harness infrastructure built to close Gaps 1-11.

Covers: agent_registry, lifecycle, task_poller, output_capture,
coordination, verification, budget enforcement.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Agent Registry ────────────────────────────────────────────────────────────

class TestAgentRegistry:
    def test_import(self):
        from sos.kernel.agent_registry import get_all_agents
        agents = get_all_agents()
        assert len(agents) >= 10

    def test_agent_fields(self):
        from sos.kernel.agent_registry import get_agent
        kasra = get_agent("kasra")
        assert kasra is not None
        assert kasra.name == "kasra"
        assert kasra.type.value == "tmux"
        assert kasra.session == "kasra"
        assert len(kasra.idle_patterns) > 0

    def test_executors_exclude_coordinators(self):
        from sos.kernel.agent_registry import get_executor_agents
        executors = get_executor_agents()
        assert "worker" in executors
        assert "kasra" not in executors
        assert "mumega" not in executors
        assert "athena" not in executors
        assert "river" not in executors

    def test_coordinators(self):
        from sos.kernel.agent_registry import get_coordinator_agents
        coords = get_coordinator_agents()
        assert "kasra" in coords
        assert "mumega" in coords
        assert "athena" in coords
        assert "worker" not in coords

    def test_is_coordinator(self):
        from sos.kernel.agent_registry import is_coordinator
        assert is_coordinator("kasra") is True
        assert is_coordinator("river") is True  # oracle counts
        assert is_coordinator("worker") is False
        assert is_coordinator("nonexistent") is False

    def test_tmux_agents(self):
        from sos.kernel.agent_registry import get_tmux_agents
        tmux = get_tmux_agents()
        assert "kasra" in tmux
        assert "mumcp" in tmux
        assert "worker" not in tmux  # openclaw

    def test_capture_agents(self):
        from sos.kernel.agent_registry import get_capture_agents
        capture = get_capture_agents()
        assert isinstance(capture, dict)
        assert capture["kasra"] == "kasra"
        assert "worker" not in capture  # openclaw, no tmux session

    def test_wake_routing(self):
        from sos.kernel.agent_registry import get_wake_routing
        routing = get_wake_routing()
        assert routing["kasra"] == "tmux"
        assert routing["athena"] == "openclaw"
        assert routing["worker"] == "openclaw"

    def test_skill_match(self):
        from sos.kernel.agent_registry import check_skill_match
        assert check_skill_match("worker", "run seo audit") is True
        assert check_skill_match("dandan", "dental outreach") is True
        assert check_skill_match("dandan", "seo audit") is False


class TestProjectRegistry:
    def test_import(self):
        from sos.kernel.project_registry import get_all_projects
        projects = get_all_projects()
        assert "workspace" in projects
        assert "sos" in projects

    def test_bootstraps_default_registry(self, tmp_path, monkeypatch):
        from sos.kernel import project_registry

        registry_file = tmp_path / "projects.json"
        monkeypatch.setattr(project_registry, "PROJECTS_FILE", registry_file)

        projects = project_registry.get_all_projects()
        assert registry_file.exists()
        assert "workspace" in projects
        assert projects["workspace"].warm_policy == "warm"

    def test_resolves_best_project_for_path(self, tmp_path, monkeypatch):
        from sos.kernel import project_registry

        registry_file = tmp_path / "projects.json"
        payload = {
            "_version": 1,
            "projects": {
                "workspace": {
                    "repo_path": str(tmp_path),
                    "preferred_agent": "codex",
                    "preferred_model": "gpt-5.4",
                    "warm_policy": "warm",
                    "context_files": [],
                },
                "nested": {
                    "repo_path": str(tmp_path / "projects" / "nested"),
                    "preferred_agent": "claude-code",
                    "preferred_model": "claude-sonnet",
                    "warm_policy": "cold",
                    "context_files": ["README.md"],
                },
            },
        }
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(json.dumps(payload))
        monkeypatch.setattr(project_registry, "PROJECTS_FILE", registry_file)

        resolved = project_registry.resolve_project_for_path(
            str(tmp_path / "projects" / "nested" / "src" / "app.py")
        )
        assert resolved is not None
        assert resolved.name == "nested"
        assert resolved.preferred_agent == "claude-code"

    def test_context_files_resolve_relative_to_repo(self, tmp_path, monkeypatch):
        from sos.kernel import project_registry

        registry_file = tmp_path / "projects.json"
        repo_dir = tmp_path / "repo"
        payload = {
            "_version": 1,
            "projects": {
                "demo": {
                    "repo_path": str(repo_dir),
                    "preferred_agent": "codex",
                    "preferred_model": "gpt-5.4",
                    "warm_policy": "cold",
                    "context_files": ["README.md", "docs/start.md"],
                }
            },
        }
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(json.dumps(payload))
        monkeypatch.setattr(project_registry, "PROJECTS_FILE", registry_file)

        files = project_registry.get_context_files("demo")
        assert files == (
            str(repo_dir / "README.md"),
            str(repo_dir / "docs/start.md"),
        )


# ── Lifecycle ─────────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_import(self):
        from sos.services.health.lifecycle import AGENT_DEFS, run_cycle
        assert len(AGENT_DEFS) >= 10
        assert "kasra" in AGENT_DEFS

    def test_agent_defs_from_registry(self):
        from sos.services.health.lifecycle import AGENT_DEFS
        kasra = AGENT_DEFS["kasra"]
        assert kasra["type"] == "tmux"
        assert kasra["session"] == "kasra"
        assert "❯" in kasra["idle_patterns"]

    def test_state_file_ops(self):
        from sos.services.health.lifecycle import load_agent_state, save_agent_state
        save_agent_state("_test_agent", {"test": True, "value": 42})
        state = load_agent_state("_test_agent")
        assert state["test"] is True
        assert state["value"] == 42
        # Cleanup
        state_file = Path.home() / ".sos" / "state" / "_test_agent.json"
        state_file.unlink(missing_ok=True)


# ── Task Poller ───────────────────────────────────────────────────────────────

class TestTaskPoller:
    def test_import(self):
        from sos.services.health.task_poller import AGENT_ROUTING
        assert "worker" in AGENT_ROUTING
        assert "kasra" not in AGENT_ROUTING

    def test_routing_from_registry(self):
        from sos.services.health.task_poller import AGENT_ROUTING
        assert AGENT_ROUTING["worker"]["type"] == "openclaw"
        # mumcp is a specialist, not an executor — should NOT be in routing
        assert "mumcp" not in AGENT_ROUTING
        # coordinators should never be in routing
        assert "kasra" not in AGENT_ROUTING
        assert "athena" not in AGENT_ROUTING

    def test_delivered_tasks_persistence(self):
        from sos.services.health.task_poller import (
            _load_delivered_tasks, _save_delivered_tasks, DELIVERED_TASKS_FILE,
        )
        test_ids = {"test-1", "test-2", "test-3"}
        _save_delivered_tasks(test_ids)
        loaded = _load_delivered_tasks()
        assert "test-1" in loaded
        assert "test-2" in loaded


# ── Output Capture ────────────────────────────────────────────────────────────

class TestOutputCapture:
    def test_import(self):
        from sos.services.health.output_capture import CAPTURE_AGENTS
        assert isinstance(CAPTURE_AGENTS, dict)
        assert "kasra" in CAPTURE_AGENTS

    def test_capture_from_registry(self):
        from sos.services.health.output_capture import CAPTURE_AGENTS
        assert CAPTURE_AGENTS["kasra"] == "kasra"
        assert "worker" not in CAPTURE_AGENTS

    def test_structured_output_parsing(self):
        from sos.services.health.output_capture import parse_structured_output
        diff = """Some output
RESULT: task_id=abc123 status=completed
SUMMARY: Fixed the pricing page hero section
VERIFY: Check https://example.com/pricing
Done with work."""
        events = parse_structured_output("worker", diff)
        assert len(events) >= 1
        result_event = [e for e in events if e["type"] == "task_result"][0]
        assert result_event["task_id"] == "abc123"
        assert result_event["status"] == "completed"
        assert result_event["summary"] == "Fixed the pricing page hero section"
        assert "example.com" in result_event["verify"]


# ── Coordination ──────────────────────────────────────────────────────────────

class TestCoordination:
    def test_import(self):
        from sos.kernel.coordination import Coordinator
        assert Coordinator is not None


# ── Verification ──────────────────────────────────────────────────────────────

class TestVerification:
    def test_import(self):
        from sos.kernel.verification import verify_action, parse_verify_line
        assert verify_action is not None

    def test_parse_url(self):
        from sos.kernel.verification import parse_verify_line
        method, target, match = parse_verify_line("Check https://example.com/page — should show hero")
        assert method == "url_check"
        assert target == "https://example.com/page"
        assert "hero" in match.lower()

    def test_parse_file(self):
        from sos.kernel.verification import parse_verify_line
        method, target, _ = parse_verify_line("File exists: /tmp/output.json")
        assert method == "file_exists"
        assert target == "/tmp/output.json"

    def test_parse_git(self):
        from sos.kernel.verification import parse_verify_line
        method, target, _ = parse_verify_line("git commit abc123")
        assert method == "git_check"
        assert target == "abc123"


# ── Budget Enforcement ────────────────────────────────────────────────────────

class TestBudgetEnforcement:
    def test_can_spend_import(self):
        from sos.services.economy.metabolism import can_spend
        result = can_spend("mumega", 0.01)
        assert "allowed" in result
        assert "budget" in result
        assert "spent" in result

    def test_can_spend_blocks_over_budget(self):
        from sos.services.economy.metabolism import can_spend
        result = can_spend("reserve", 999.0)
        assert result["allowed"] is False
        assert result["reason"] == "insufficient_budget"

    def test_can_spend_allows_normal(self):
        from sos.services.economy.metabolism import can_spend
        result = can_spend("mumega", 0.0)
        assert result["allowed"] is True
