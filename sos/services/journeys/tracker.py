"""Journey Tracker — progression system for workers.

Tracks agent progress through journey paths (builder, wordsmith, scout,
connector, guardian). Evaluates milestone conditions against real data
from Squad Service and conductance network.

Storage: ~/.sos/journeys/{agent}/progress.json
Paths: sos/services/journeys/paths/*.yaml
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("sos.journeys")

PATHS_DIR = Path(__file__).parent / "paths"
JOURNEYS_DIR = Path.home() / ".sos" / "journeys"
SQUAD_URL = os.environ.get("SQUAD_URL", "http://127.0.0.1:8060")
SQUAD_TOKEN = os.environ.get("SOS_SYSTEM_TOKEN", "")


def _squad_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SQUAD_TOKEN}"} if SQUAD_TOKEN else {}


def _load_path(name: str) -> dict[str, Any]:
    """Load a journey path definition from YAML."""
    path_file = PATHS_DIR / f"{name}.yaml"
    if not path_file.exists():
        raise FileNotFoundError(f"Journey path not found: {name}")
    return yaml.safe_load(path_file.read_text())


def _load_all_paths() -> dict[str, dict[str, Any]]:
    """Load all journey path definitions."""
    paths = {}
    for f in PATHS_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text())
            paths[data["name"]] = data
        except Exception as exc:
            logger.warning("Failed to load path %s: %s", f.name, exc)
    return paths


def _load_progress(agent: str) -> dict[str, Any]:
    """Load agent's journey progress from disk."""
    progress_file = JOURNEYS_DIR / agent / "progress.json"
    if progress_file.exists():
        try:
            return json.loads(progress_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"agent": agent, "paths": {}, "badges": [], "total_mind_earned": 0}


def _save_progress(agent: str, progress: dict[str, Any]) -> None:
    """Persist agent's journey progress to disk."""
    progress_dir = JOURNEYS_DIR / agent
    progress_dir.mkdir(parents=True, exist_ok=True)
    progress_file = progress_dir / "progress.json"
    progress["updated_at"] = datetime.now(timezone.utc).isoformat()
    progress_file.write_text(json.dumps(progress, indent=2))


class JourneyTracker:
    """Tracks agent progression through journey paths."""

    def __init__(self, tenant: str | None = None) -> None:
        self.tenant = tenant
        self.paths = _load_all_paths()

    def start_journey(self, agent: str, path_name: str) -> dict[str, Any]:
        """Start an agent on a journey path."""
        if path_name not in self.paths:
            return {"error": f"Unknown path: {path_name}"}

        progress = _load_progress(agent)
        if path_name in progress["paths"]:
            return {"error": f"Agent {agent} already on {path_name} path"}

        path_def = self.paths[path_name]
        first_milestone = path_def["milestones"][0]["id"]

        progress["paths"][path_name] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "current_milestone": first_milestone,
            "completed_milestones": [],
        }
        _save_progress(agent, progress)

        logger.info("Journey started: %s → %s (first: %s)", agent, path_name, first_milestone)
        return {
            "agent": agent,
            "path": path_name,
            "display": path_def["display"],
            "current_milestone": first_milestone,
        }

    def check_progress(self, agent: str) -> list[dict[str, Any]]:
        """Check all active journeys and progress for an agent."""
        progress = _load_progress(agent)
        results: list[dict[str, Any]] = []

        for path_name, path_progress in progress["paths"].items():
            if path_name not in self.paths:
                continue
            path_def = self.paths[path_name]
            current_id = path_progress["current_milestone"]
            completed = path_progress["completed_milestones"]

            # Find current milestone definition
            current_def = None
            for m in path_def["milestones"]:
                if m["id"] == current_id:
                    current_def = m
                    break

            total = len(path_def["milestones"])
            done = len(completed)

            results.append({
                "path": path_name,
                "display": path_def["display"],
                "current_milestone": current_id,
                "current_title": current_def["title"] if current_def else "unknown",
                "completed": done,
                "total": total,
                "progress_pct": round(done / total * 100) if total else 0,
                "badges": [m for m in completed],
            })

        return results

    def evaluate_milestone(self, agent: str, path_name: str, milestone_id: str) -> bool:
        """Check if an agent meets the conditions for a milestone."""
        if path_name not in self.paths:
            return False

        path_def = self.paths[path_name]
        milestone = None
        for m in path_def["milestones"]:
            if m["id"] == milestone_id:
                milestone = m
                break
        if not milestone:
            return False

        condition = milestone.get("condition", {})
        stats = self._get_agent_stats(agent)

        # Check tasks_completed
        if "tasks_completed" in condition:
            if stats["tasks_completed"] < condition["tasks_completed"]:
                return False

        # Check tasks_completed_with_label
        if "tasks_completed_with_label" in condition:
            label_cond = condition["tasks_completed_with_label"]
            label = label_cond["label"]
            count = label_cond["count"]
            label_count = stats.get("label_counts", {}).get(label, 0)
            if label_count < count:
                return False

        # Check min_effectiveness
        if "min_effectiveness" in condition:
            if stats.get("effectiveness", 0) < condition["min_effectiveness"]:
                return False

        # Check min_conductance
        if "min_conductance" in condition:
            if stats.get("max_conductance", 0) < condition["min_conductance"]:
                return False

        # Check min_coherence
        if "min_coherence" in condition:
            if stats.get("coherence", 0.5) < condition["min_coherence"]:
                return False

        return True

    def complete_milestone(self, agent: str, path_name: str, milestone_id: str) -> dict[str, Any]:
        """Award MIND + badge + unlock next milestone."""
        if not self.evaluate_milestone(agent, path_name, milestone_id):
            return {"error": "Conditions not met"}

        progress = _load_progress(agent)
        if path_name not in progress["paths"]:
            return {"error": f"Agent not on {path_name} path"}

        path_progress = progress["paths"][path_name]
        if milestone_id in path_progress["completed_milestones"]:
            return {"error": "Already completed"}

        path_def = self.paths[path_name]
        milestone = None
        for m in path_def["milestones"]:
            if m["id"] == milestone_id:
                milestone = m
                break
        if not milestone:
            return {"error": "Milestone not found"}

        # Complete
        path_progress["completed_milestones"].append(milestone_id)
        reward = milestone.get("reward_mind", 0)
        badge = milestone.get("badge", "")
        unlocks = milestone.get("unlocks", [])

        if badge and badge not in progress["badges"]:
            progress["badges"].append(badge)
        progress["total_mind_earned"] = progress.get("total_mind_earned", 0) + reward

        # Advance to next milestone
        if unlocks:
            path_progress["current_milestone"] = unlocks[0]
        else:
            path_progress["current_milestone"] = "elder"

        path_progress["last_completed_at"] = datetime.now(timezone.utc).isoformat()
        _save_progress(agent, progress)

        logger.info(
            "Milestone completed: %s → %s/%s (+%d MIND, badge: %s)",
            agent, path_name, milestone_id, reward, badge,
        )

        return {
            "agent": agent,
            "path": path_name,
            "milestone": milestone_id,
            "title": milestone["title"],
            "reward_mind": reward,
            "badge": badge,
            "next": unlocks[0] if unlocks else "elder",
        }

    def recommend_journey(self, agent: str) -> str:
        """Recommend the best journey path based on agent's skills and conductance."""
        from sos.kernel.conductance import _load_conductance

        G = _load_conductance()
        agent_G = G.get(agent, {})

        best_path = ""
        best_score = -1.0

        for path_name, path_def in self.paths.items():
            path_skills = set(path_def.get("skills", []))
            score = sum(agent_G.get(s, 0.0) for s in path_skills)
            if score > best_score:
                best_score = score
                best_path = path_name

        # If no conductance data, match by declared skills from agent registry
        if best_score <= 0:
            try:
                from sos.kernel.agent_registry import get_skills_for_agent
                agent_skills = set(get_skills_for_agent(agent))
                for path_name, path_def in self.paths.items():
                    path_skills = set(path_def.get("skills", []))
                    overlap = len(agent_skills & path_skills)
                    if overlap > best_score:
                        best_score = overlap
                        best_path = path_name
            except ImportError:
                pass

        return best_path or "builder"  # Default to builder

    def get_leaderboard(self, path_name: str | None = None) -> list[dict[str, Any]]:
        """Get leaderboard — who's furthest on each path."""
        leaders: list[dict[str, Any]] = []

        if not JOURNEYS_DIR.exists():
            return leaders

        for agent_dir in JOURNEYS_DIR.iterdir():
            if not agent_dir.is_dir():
                continue
            agent = agent_dir.name
            progress = _load_progress(agent)

            for pname, pprog in progress["paths"].items():
                if path_name and pname != path_name:
                    continue
                leaders.append({
                    "agent": agent,
                    "path": pname,
                    "completed": len(pprog["completed_milestones"]),
                    "current": pprog["current_milestone"],
                    "badges": len(progress["badges"]),
                    "total_mind": progress.get("total_mind_earned", 0),
                })

        leaders.sort(key=lambda x: (x["completed"], x["total_mind"]), reverse=True)
        return leaders

    def auto_evaluate(self, agent: str) -> list[dict[str, Any]]:
        """Check all active milestones and complete any that are met.

        Called after task.complete() to auto-progress.
        """
        progress = _load_progress(agent)
        completions: list[dict[str, Any]] = []

        for path_name, path_progress in progress["paths"].items():
            current = path_progress["current_milestone"]
            if current == "elder":
                continue
            if self.evaluate_milestone(agent, path_name, current):
                result = self.complete_milestone(agent, path_name, current)
                if not result.get("error"):
                    completions.append(result)

        return completions

    def _get_agent_stats(self, agent: str) -> dict[str, Any]:
        """Gather agent stats from Squad Service and conductance network."""
        stats: dict[str, Any] = {
            "tasks_completed": 0,
            "label_counts": {},
            "effectiveness": 0.5,
            "max_conductance": 0.0,
            "coherence": 0.5,
        }

        # Tasks from Squad Service
        try:
            import requests
            resp = requests.get(
                f"{SQUAD_URL}/tasks",
                params={"assignee": agent, "status": "done"},
                headers=_squad_headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                tasks = resp.json()
                if isinstance(tasks, dict):
                    tasks = tasks.get("tasks", [])
                stats["tasks_completed"] = len(tasks)
                for t in tasks:
                    for label in (t.get("labels") or []):
                        stats["label_counts"][label] = stats["label_counts"].get(label, 0) + 1
        except Exception:
            pass

        # Conductance
        try:
            from sos.kernel.conductance import _load_conductance
            G = _load_conductance()
            agent_G = G.get(agent, {})
            if agent_G:
                stats["max_conductance"] = max(agent_G.values())
        except Exception:
            pass

        # Coherence from state file
        state_file = Path.home() / ".sos" / "state" / f"{agent}.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                stats["coherence"] = state.get("coherence_C", 0.5)
            except Exception:
                pass

        return stats
