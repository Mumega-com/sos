"""
Hive Evolution — Self-Improving Swarm Loop

The AutoAgent pattern applied to Mumega's Hive Bridge:
  Worker generates → Judge scores → Winner stored → Loser rewritten

Uses Mirror API to store winning configs. Next time the same task type
runs, it starts with the best-performing prompt + model combination.

This is the mechanism that turns the swarm from a parallel executor
into a self-improving animal.

Integration points:
  - AsyncHiveBridge.swarm_critique() → feeds scores here
  - Mirror API (:8844) → stores/retrieves winning configs
  - Metabolism (~/scripts/metabolism.py) → respects budget per fuel grade
"""

import json
import time
import hashlib
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

# Mirror API for persistent memory
from kernel.config import MIRROR_URL, MIRROR_TOKEN


@dataclass
class TaskRecipe:
    """A learned recipe for a specific task type."""
    task_type: str                    # e.g., "content_gen", "lead_qualify", "code_review"
    system_prompt: str                # The prompt that worked
    model: str                       # The model that worked
    fuel_grade: str                   # diesel, regular, premium, aviation
    avg_score: float                  # Rolling average score (0.0 - 1.0)
    successes: int = 0               # Times this recipe succeeded
    failures: int = 0                # Times this recipe failed
    total_cost_usd: float = 0.0      # Total cost across all runs
    avg_latency_ms: float = 0.0      # Average latency
    last_used: str = ""              # ISO timestamp
    evolution_count: int = 0         # How many times this recipe has been rewritten

    @property
    def efficiency(self) -> float:
        """Score per dollar spent. Higher = more efficient."""
        if self.total_cost_usd == 0:
            return self.avg_score * 1000  # Free models get massive bonus
        return self.avg_score / self.total_cost_usd

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["efficiency"] = self.efficiency
        return d


# Fuel grade mapping
FUEL_GRADES = {
    "diesel": {
        "models": [
            "gemma-4-31b-it", "gemma-4-26b-a4b-it",
            "gemini-2.0-flash-exp", "gpt-4o-mini",
            "claude-haiku-4-5",
        ],
        "max_cost_per_1m": 0.0,
        "description": "Free tier — content, social, bulk tasks",
    },
    "regular": {
        "models": [
            "grok-4-1-fast-reasoning", "deepseek-chat",
            "deepseek-v3.2", "gemini-3-flash-preview",
        ],
        "max_cost_per_1m": 0.50,
        "description": "Cheap paid — support, code, data processing",
    },
    "premium": {
        "models": [
            "gpt-4o", "gemini-2.5-pro", "grok-4.1",
        ],
        "max_cost_per_1m": 5.00,
        "description": "Mid-tier — complex workflows, edge cases",
    },
    "aviation": {
        "models": [
            "claude-opus-4-6", "gpt-5.2", "claude-sonnet-4-6",
        ],
        "max_cost_per_1m": 25.00,
        "description": "Expensive — architecture, critique, strategy",
    },
}


def get_fuel_grade(model: str) -> str:
    """Determine fuel grade for a model."""
    for grade, config in FUEL_GRADES.items():
        if model in config["models"]:
            return grade
    return "regular"  # default


class HiveEvolution:
    """
    The self-improving feedback loop.

    1. Before a task: check Mirror for winning recipe
    2. Execute with best known recipe (or default)
    3. Score the output via critique swarm
    4. If score > best: save new recipe to Mirror
    5. If score < threshold: escalate fuel grade and retry
    6. Over time: recipes evolve, costs decrease, quality increases
    """

    def __init__(self):
        self._recipe_cache: Dict[str, TaskRecipe] = {}
        self._load_cache()

    def _load_cache(self):
        """Load recipe cache from Mirror API."""
        try:
            import requests
            r = requests.post(
                f"{MIRROR_URL}/search",
                json={"query": "hive_evolution recipe", "top_k": 50, "agent_filter": "hive_evolution"},
                headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
                timeout=5,
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                for result in results:
                    raw = result.get("raw_data", {})
                    if raw.get("recipe"):
                        recipe_data = raw["recipe"]
                        task_type = recipe_data.get("task_type", "")
                        if task_type:
                            self._recipe_cache[task_type] = TaskRecipe(**recipe_data)
                logger.info(f"Loaded {len(self._recipe_cache)} evolution recipes from Mirror")
        except Exception as e:
            logger.warning(f"Could not load recipes from Mirror: {e}")

    def get_recipe(self, task_type: str) -> Optional[TaskRecipe]:
        """Get the best known recipe for a task type."""
        return self._recipe_cache.get(task_type)

    def get_best_config(self, task_type: str, default_model: str = "gemma-4-31b-it",
                        default_prompt: str = "") -> Tuple[str, str]:
        """
        Get the best model + prompt for a task type.
        Returns (model, system_prompt).
        """
        recipe = self.get_recipe(task_type)
        if recipe and recipe.avg_score > 0.6:
            logger.info(
                f"Using evolved recipe for '{task_type}': "
                f"model={recipe.model} score={recipe.avg_score:.2f} "
                f"grade={recipe.fuel_grade} runs={recipe.successes}"
            )
            return recipe.model, recipe.system_prompt
        return default_model, default_prompt

    def record_result(self, task_type: str, model: str, system_prompt: str,
                      score: float, cost_usd: float, latency_ms: float):
        """
        Record a task result and evolve the recipe if it's the new best.
        """
        fuel_grade = get_fuel_grade(model)
        existing = self._recipe_cache.get(task_type)

        if existing is None:
            # First time seeing this task type — create recipe
            recipe = TaskRecipe(
                task_type=task_type,
                system_prompt=system_prompt,
                model=model,
                fuel_grade=fuel_grade,
                avg_score=score,
                successes=1 if score > 0.5 else 0,
                failures=0 if score > 0.5 else 1,
                total_cost_usd=cost_usd,
                avg_latency_ms=latency_ms,
                last_used=datetime.utcnow().isoformat(),
            )
            self._recipe_cache[task_type] = recipe
            self._save_recipe(recipe)
            logger.info(f"NEW recipe for '{task_type}': score={score:.2f} model={model}")
            return

        # Update rolling stats
        total_runs = existing.successes + existing.failures
        existing.avg_score = (existing.avg_score * total_runs + score) / (total_runs + 1)
        existing.avg_latency_ms = (existing.avg_latency_ms * total_runs + latency_ms) / (total_runs + 1)
        existing.total_cost_usd += cost_usd
        existing.last_used = datetime.utcnow().isoformat()

        if score > 0.5:
            existing.successes += 1
        else:
            existing.failures += 1

        # Evolution: if this run beat the current recipe, adopt it
        if score > existing.avg_score and self._should_evolve(existing, score, model, fuel_grade):
            logger.info(
                f"EVOLVING recipe for '{task_type}': "
                f"score {existing.avg_score:.2f} → {score:.2f}, "
                f"model {existing.model} → {model}"
            )
            existing.system_prompt = system_prompt
            existing.model = model
            existing.fuel_grade = fuel_grade
            existing.evolution_count += 1

        self._recipe_cache[task_type] = existing
        self._save_recipe(existing)

    def _should_evolve(self, existing: TaskRecipe, new_score: float,
                       new_model: str, new_grade: str) -> bool:
        """
        Decide whether to evolve the recipe.
        Prefer: higher score at same or lower fuel grade.
        Only escalate fuel grade if score improvement is significant.
        """
        grade_order = {"diesel": 0, "regular": 1, "premium": 2, "aviation": 3}
        existing_grade = grade_order.get(existing.fuel_grade, 1)
        new_grade_val = grade_order.get(new_grade, 1)

        # Same or cheaper fuel + better score = always evolve
        if new_grade_val <= existing_grade and new_score > existing.avg_score:
            return True

        # More expensive fuel requires significant improvement (>20%)
        if new_grade_val > existing_grade:
            improvement = (new_score - existing.avg_score) / max(existing.avg_score, 0.01)
            return improvement > 0.20

        return False

    def suggest_escalation(self, task_type: str) -> Optional[str]:
        """
        If a task type is performing poorly, suggest escalating to a higher fuel grade.
        """
        recipe = self.get_recipe(task_type)
        if not recipe:
            return None

        if recipe.avg_score < 0.5 and recipe.successes < recipe.failures:
            current_grade = recipe.fuel_grade
            grade_order = ["diesel", "regular", "premium", "aviation"]
            idx = grade_order.index(current_grade) if current_grade in grade_order else 0
            if idx < len(grade_order) - 1:
                next_grade = grade_order[idx + 1]
                next_models = FUEL_GRADES[next_grade]["models"]
                return next_models[0] if next_models else None

        return None

    def _save_recipe(self, recipe: TaskRecipe):
        """Persist recipe to Mirror API."""
        try:
            import requests
            context_id = f"recipe_{recipe.task_type}"
            requests.post(
                f"{MIRROR_URL}/store",
                json={
                    "agent": "hive_evolution",
                    "context_id": context_id,
                    "text": f"Evolution recipe for {recipe.task_type}: model={recipe.model} score={recipe.avg_score:.2f} grade={recipe.fuel_grade}",
                    "epistemic_truths": [
                        f"{recipe.task_type} best model: {recipe.model}",
                        f"{recipe.task_type} avg score: {recipe.avg_score:.2f}",
                        f"{recipe.task_type} fuel grade: {recipe.fuel_grade}",
                    ],
                    "core_concepts": ["hive_evolution", "recipe", recipe.task_type, recipe.fuel_grade],
                    "raw_data": {"recipe": recipe.to_dict()},
                },
                headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"Could not save recipe to Mirror: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Return current evolution status."""
        recipes = []
        for task_type, recipe in self._recipe_cache.items():
            recipes.append({
                "task_type": task_type,
                "model": recipe.model,
                "fuel_grade": recipe.fuel_grade,
                "avg_score": round(recipe.avg_score, 3),
                "efficiency": round(recipe.efficiency, 2),
                "runs": recipe.successes + recipe.failures,
                "evolution_count": recipe.evolution_count,
            })
        recipes.sort(key=lambda r: r["efficiency"], reverse=True)
        return {
            "total_recipes": len(recipes),
            "recipes": recipes,
        }


# Singleton
_evolution_instance: Optional[HiveEvolution] = None


def get_evolution() -> HiveEvolution:
    global _evolution_instance
    if _evolution_instance is None:
        _evolution_instance = HiveEvolution()
    return _evolution_instance
