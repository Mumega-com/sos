#!/usr/bin/env python3
"""
Squad Router — matches tasks to skills, loads context, executes, saves results.

Flow: task arrives → load shared state → match skill → run → save result

Skills are plain Python functions: def run(task, ctx) -> SkillResult
Matching: labels first, keyword fallback on title/description.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [router] %(message)s")
logger = logging.getLogger("squad_router")

from kernel.config import MIRROR_URL, MIRROR_TOKEN, SOVEREIGN_SQUADS_DIR

MIRROR_HEADERS = {
    "Authorization": f"Bearer {MIRROR_TOKEN}",
    "Content-Type": "application/json",
}
STATE_DIR = Path(SOVEREIGN_SQUADS_DIR)


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class RouterContext:
    """Context passed to skills — shared state + task info."""
    project: str
    squad_id: str
    task_id: str
    task_title: str
    task_description: str
    labels: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """Return type from skill execution."""
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: str = ""


# ── Shared State ──────────────────────────────────────────────────────────────

def save_state(squad_id: str, key: str, data: dict[str, Any]) -> None:
    """Save squad state to local JSON (Mirror integration via squad_state.py)."""
    squad_dir = STATE_DIR / squad_id
    squad_dir.mkdir(parents=True, exist_ok=True)
    path = squad_dir / f"{key}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info(f"State saved: {squad_id}/{key}")


def load_state(squad_id: str, key: str) -> dict[str, Any]:
    """Load squad state from local JSON."""
    path = STATE_DIR / squad_id / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def load_all_state(squad_id: str) -> dict[str, Any]:
    """Load all state for a squad."""
    squad_dir = STATE_DIR / squad_id
    if not squad_dir.exists():
        return {}
    state = {}
    for f in squad_dir.glob("*.json"):
        state[f.stem] = json.loads(f.read_text())
    return state


# ── Skill Registry ────────────────────────────────────────────────────────────

# Skills register here: label/keyword → function
# Each skill: def run(task: dict, ctx: RouterContext) -> SkillResult
SKILLS: dict[str, Callable] = {}
LABEL_MAP: dict[str, str] = {}  # label → skill name
KEYWORD_MAP: dict[str, str] = {}  # keyword → skill name


def register_skill(
    name: str,
    func: Callable,
    labels: list[str] | None = None,
    keywords: list[str] | None = None,
) -> None:
    """Register a skill function with matching rules."""
    SKILLS[name] = func
    for label in labels or []:
        LABEL_MAP[label] = name
    for kw in keywords or []:
        KEYWORD_MAP[kw.lower()] = name


def match_skill(task: dict) -> Optional[str]:
    """Match a task to a skill. Labels first, then keyword fallback."""
    labels = task.get("labels", [])

    # Label match (exact)
    for label in labels:
        if label in LABEL_MAP:
            return LABEL_MAP[label]

    # Keyword match on title + description
    text = f"{task.get('title', '')} {task.get('description', '')}".lower()
    for keyword, skill_name in KEYWORD_MAP.items():
        if keyword in text:
            return skill_name

    return None


# ── Router ────────────────────────────────────────────────────────────────────

def handle_task(task: dict, squad_id: str) -> SkillResult:
    """
    Main entry point. Routes a task through the squad pipeline:
    1. Load shared state
    2. Match skill
    3. Build context
    4. Execute
    5. Save result
    """
    task_id = task.get("id", "unknown")
    title = task.get("title", "")
    project = task.get("project", "")

    logger.info(f"Routing task: {title}")

    # 1. Load shared state
    state = load_all_state(squad_id)

    # 2. Match skill
    skill_name = match_skill(task)
    if not skill_name:
        logger.warning(f"No skill matched for: {title}")
        return SkillResult(
            success=False,
            error=f"No skill matched for task: {title}",
            summary="Unroutable — needs manual review or new skill",
        )

    skill_func = SKILLS[skill_name]
    logger.info(f"Matched skill: {skill_name}")

    # 3. Build context
    ctx = RouterContext(
        project=project,
        squad_id=squad_id,
        task_id=task_id,
        task_title=title,
        task_description=task.get("description", ""),
        labels=task.get("labels", []),
        state=state,
    )

    # 4. Execute
    start = time.time()
    try:
        result = skill_func(task, ctx)
    except Exception as e:
        logger.error(f"Skill {skill_name} failed: {e}")
        result = SkillResult(success=False, error=str(e))

    elapsed = time.time() - start
    logger.info(f"Skill {skill_name} completed in {elapsed:.1f}s — success={result.success}")

    # 5. Save result to shared state
    save_state(squad_id, f"result_{task_id}", {
        "skill": skill_name,
        "success": result.success,
        "summary": result.summary,
        "output": result.output,
        "elapsed_s": round(elapsed, 1),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    # 6. Mark task in Mirror
    try:
        status = "done" if result.success else "blocked"
        httpx.put(
            f"{MIRROR_URL}/tasks/{task_id}",
            json={"status": status},
            headers=MIRROR_HEADERS,
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Failed to update task {task_id}: {e}")

    return result


# ── Register SEO Skills ───────────────────────────────────────────────────────

def _register_seo_skills() -> None:
    """Register SEO skills from sovereign/skills/seo.py."""
    import asyncio

    try:
        from skills.seo import site_audit, meta_optimizer, internal_link_analyzer, schema_checker, run_full_audit
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from skills.seo import site_audit, meta_optimizer, internal_link_analyzer, schema_checker, run_full_audit

    def _run_async(coro):
        """Run async function synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    def skill_site_audit(task: dict, ctx: RouterContext) -> SkillResult:
        url = ctx.state.get("brief", {}).get("url", "")
        if not url:
            return SkillResult(success=False, error="No URL in squad state — save brief first")
        result = _run_async(site_audit(url))
        return SkillResult(success=True, output=result, summary=f"Site audit: {result.get('issue_count', 0)} issues found")

    def skill_meta_optimizer(task: dict, ctx: RouterContext) -> SkillResult:
        url = ctx.state.get("brief", {}).get("url", "")
        if not url:
            return SkillResult(success=False, error="No URL in squad state")
        result = _run_async(meta_optimizer(url))
        return SkillResult(success=True, output=result, summary=f"Meta optimizer: {result.get('pages_checked', 0)} pages checked")

    def skill_link_analyzer(task: dict, ctx: RouterContext) -> SkillResult:
        url = ctx.state.get("brief", {}).get("url", "")
        if not url:
            return SkillResult(success=False, error="No URL in squad state")
        result = _run_async(internal_link_analyzer(url))
        return SkillResult(success=True, output=result, summary=f"Link analysis: {result.get('orphan_count', 0)} orphans, {result.get('under_linked_count', 0)} under-linked")

    def skill_schema_checker(task: dict, ctx: RouterContext) -> SkillResult:
        url = ctx.state.get("brief", {}).get("url", "")
        if not url:
            return SkillResult(success=False, error="No URL in squad state")
        result = _run_async(schema_checker(url))
        return SkillResult(success=True, output=result, summary=f"Schema: {result.get('schemas_found', 0)} found, {len(result.get('missing_recommended_types', []))} missing")

    def skill_full_audit(task: dict, ctx: RouterContext) -> SkillResult:
        url = ctx.state.get("brief", {}).get("url", "")
        if not url:
            return SkillResult(success=False, error="No URL in squad state")
        result = _run_async(run_full_audit(url))
        return SkillResult(success=True, output=result, summary=f"Full audit: {result.get('total_issues', 0)} total issues")

    register_skill("site_audit", skill_site_audit,
                    labels=["audit", "technical-seo"],
                    keywords=["audit", "check site", "crawl"])

    register_skill("meta_optimizer", skill_meta_optimizer,
                    labels=["meta", "meta_optimization"],
                    keywords=["meta tag", "title tag", "description", "meta optimization"])

    register_skill("link_analyzer", skill_link_analyzer,
                    labels=["links", "internal_linking"],
                    keywords=["internal link", "link map", "orphan page", "link equity"])

    register_skill("schema_checker", skill_schema_checker,
                    labels=["schema", "schema_markup"],
                    keywords=["schema", "json-ld", "structured data", "rich results"])

    register_skill("full_audit", skill_full_audit,
                    labels=["full-audit"],
                    keywords=["full audit", "complete audit", "seo audit"])


# Auto-register on import
_register_seo_skills()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage:")
        print("  squad_router.py route <squad_id> <task_json>")
        print("  squad_router.py skills                        # list registered skills")
        print("  squad_router.py test <squad_id>               # test with DNU audit task")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "skills":
        print(f"Registered skills: {len(SKILLS)}")
        for name in SKILLS:
            labels = [l for l, s in LABEL_MAP.items() if s == name]
            keywords = [k for k, s in KEYWORD_MAP.items() if s == name]
            print(f"  {name}: labels={labels} keywords={keywords}")

    elif cmd == "test":
        squad_id = sys.argv[2] if len(sys.argv) > 2 else "seo-dnu"

        # Save DNU brief to state
        brief = {
            "url": "https://dentalnearyou.ca",
            "project": "dentalnearyou",
            "cities": ["Toronto", "Vancouver", "Calgary", "Edmonton", "Ottawa"],
        }
        save_state(squad_id, "brief", brief)

        # Test routing a task
        test_task = {
            "id": "test-001",
            "title": "Schema markup audit for DNU",
            "description": "Check JSON-LD schema on dentalnearyou.ca",
            "project": "dentalnearyou",
            "labels": ["schema", "seo"],
        }
        result = handle_task(test_task, squad_id)
        print(f"\nResult: success={result.success}")
        print(f"Summary: {result.summary}")
        if result.error:
            print(f"Error: {result.error}")

    elif cmd == "route":
        squad_id = sys.argv[2]
        task_json = sys.argv[3]
        task = json.loads(task_json)
        result = handle_task(task, squad_id)
        print(json.dumps(asdict(result), indent=2, default=str))
