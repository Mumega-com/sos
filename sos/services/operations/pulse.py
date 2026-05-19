"""Organism pulse — posts the daily-rhythm objective tree for a project.

A pulse is one heartbeat of the organism. There are three pulses per project
per day:

- **morning** — the productive root. One child per standing workflow, bounty
  + capabilities wired through, seeded fresh each dawn.
- **noon** — a lightweight health check. One ``check-health-of-objectives``
  child so the organism self-observes mid-day.
- **evening** — summarise + harvest. One ``evening-postmortem`` child (what
  closed today, why, which ones moved the needle) + one ``harvest-winners``
  child (curator promotes high-scoring artifacts into the demo bank).

Each pulse is fail-soft: if the objectives service is unreachable we log and
return an empty string so cron/systemd doesn't flap. The next tick retries.
Root IDs are dated (``<project>-<window>-YYYYMMDD``) so re-posting is safe.

Standing workflows are **tenant-owned data**, not SOS code. The pulse reads
its workflow list from a JSON file specified by the caller (the tenant repo
owns the file, SOS reads it). This keeps SOS tenant-agnostic.

CLI::

    # morning pulse with an explicit tenant workflows file
    python -m sos.services.operations.pulse \\
      --project trop \\
      --workflows-file /path/to/trop/standing_workflows.json

    # evening pulse (no children from workflows — two fixed postmortem/harvest
    # children are posted regardless)
    python -m sos.services.operations.pulse --project trop --window evening
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sos.clients.objectives import AsyncObjectivesClient

logger = logging.getLogger("sos.operations.pulse")


# ---------------------------------------------------------------------------
# Standing-workflow lookup — tenant-agnostic
# ---------------------------------------------------------------------------


def _resolve_workflows_file(project: str, workflows_file: str | None) -> str | None:
    """Resolve the workflows JSON path for ``project``.

    Resolution order:
    1. Explicit ``workflows_file`` argument.
    2. Env ``SOS_PULSE_WORKFLOWS_FILE_<PROJECT>`` (upper-cased project slug).
    3. Env ``SOS_PULSE_WORKFLOWS_FILE`` (shared across all projects).
    4. ``None`` — pulse emits the root with no children.
    """
    if workflows_file:
        return workflows_file
    per_project = os.environ.get(f"SOS_PULSE_WORKFLOWS_FILE_{project.upper()}")
    if per_project:
        return per_project
    shared = os.environ.get("SOS_PULSE_WORKFLOWS_FILE")
    if shared:
        return shared
    return None


def load_standing_workflows(
    project: str,
    *,
    workflows_file: str | None = None,
) -> list[dict[str, Any]]:
    """Return the list of standing workflows for ``project``.

    Reads a JSON array from the resolved workflows-file path. Each entry is
    a dict with keys ``name``, ``bounty_mind``, ``tags``,
    ``capabilities_required``, ``description``. Missing/unreadable file
    returns ``[]`` — the pulse still emits the root so the tree is visible,
    just with no children.
    """
    path = _resolve_workflows_file(project, workflows_file)
    if not path:
        logger.info(
            "pulse: no workflows file configured for project=%s "
            "(set SOS_PULSE_WORKFLOWS_FILE_%s or pass --workflows-file)",
            project,
            project.upper(),
        )
        return []

    try:
        raw = Path(path).read_text()
        data = json.loads(raw)
    except FileNotFoundError:
        logger.warning("pulse: workflows file not found: %s", path)
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("pulse: failed to read workflows file %s: %s", path, exc)
        return []

    if not isinstance(data, list):
        logger.warning(
            "pulse: workflows file %s must be a JSON array, got %s",
            path,
            type(data).__name__,
        )
        return []
    return [wf for wf in data if isinstance(wf, dict)]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _today_stamp() -> str:
    """YYYYMMDD in UTC — stable root-id suffix per pulse day."""
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _root_id(project: str, window: str, day: str) -> str:
    """Canonical slug ID for a pulse's root objective."""
    return f"{project}-{window}-{day}"


async def _safe_create(
    client: AsyncObjectivesClient,
    *,
    title: str,
    parent_id: str | None = None,
    description: str = "",
    bounty_mind: int = 0,
    tags: list[str] | None = None,
    capabilities_required: list[str] | None = None,
    project: str,
    created_by: str,
) -> str | None:
    """Call ``client.create`` and log-swallow any exception.

    Returns the new objective id on success, ``None`` on failure. Keeps the
    pulse fail-soft without per-call try/except boilerplate.
    """
    try:
        obj = await client.create(
            title=title,
            parent_id=parent_id,
            description=description,
            bounty_mind=bounty_mind,
            tags=list(tags or []),
            capabilities_required=list(capabilities_required or []),
            subscribers=[],
            tenant_id=project,
            project=project,
            created_by=created_by,
        )
        return obj.id
    except Exception as exc:
        logger.warning(
            "pulse: failed to post %s (parent=%s): %s",
            title,
            parent_id,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Morning pulse — productive daily-rhythm root
# ---------------------------------------------------------------------------


async def post_daily_rhythm(
    project: str,
    *,
    client: AsyncObjectivesClient | None = None,
    workflows: list[dict[str, Any]] | None = None,
    workflows_file: str | None = None,
) -> str:
    """Post the morning daily-rhythm root + children for ``project``.

    Args:
        project: Tenant project slug.
        client: Optional pre-constructed client — useful for tests.
        workflows: Explicit workflow list. If ``None``, falls back to
            reading a JSON file (see ``workflows_file`` / env vars).
        workflows_file: Path to a JSON array of workflow dicts. Ignored if
            ``workflows`` is provided.
    """
    day = _today_stamp()
    if workflows is None:
        workflows = load_standing_workflows(project, workflows_file=workflows_file)

    if client is None:
        client = AsyncObjectivesClient()

    root_id = await _safe_create(
        client,
        title=f"{project} daily rhythm — {day}",
        description=(
            f"Standing daily-rhythm tree for project={project}. "
            f"Children come from the tenant's standing workflows file."
        ),
        bounty_mind=0,
        tags=[project, "daily-rhythm", "pulse"],
        project=project,
        created_by=f"pulse:{project}",
    )
    if not root_id:
        return ""

    logger.info("pulse: posted morning root %s (project=%s)", root_id, project)

    for wf in workflows:
        child_id = await _safe_create(
            client,
            title=wf["name"],
            parent_id=root_id,
            description=wf.get("description", ""),
            bounty_mind=int(wf.get("bounty_mind", 0)),
            tags=list(wf.get("tags", [])),
            capabilities_required=list(wf.get("capabilities_required", [])),
            project=project,
            created_by=f"pulse:{project}",
        )
        if child_id:
            logger.info(
                "pulse: posted child %s (%s, bounty=%d)",
                child_id,
                wf["name"],
                wf.get("bounty_mind", 0),
            )

    return root_id


# Alias — S6 introduced the window-specific names; keep both.
post_morning_pulse = post_daily_rhythm


# ---------------------------------------------------------------------------
# Noon pulse — health check
# ---------------------------------------------------------------------------


async def post_noon_pulse(
    project: str,
    *,
    client: AsyncObjectivesClient | None = None,
    workflows: list[dict[str, Any]] | None = None,
    workflows_file: str | None = None,
) -> str:
    """Post the noon health-check root + one child for ``project``.

    Noon has one fixed child (``check-health-of-objectives``). ``workflows``
    / ``workflows_file`` are accepted for API uniformity with the other
    windows but are currently ignored.
    """
    del workflows, workflows_file  # noon is not workflow-driven today
    day = _today_stamp()
    if client is None:
        client = AsyncObjectivesClient()

    root_id = await _safe_create(
        client,
        title=f"{project} noon pulse — {day}",
        description=(
            f"Midday health-check pulse for project={project}. One child "
            f"verifies that the morning tree is moving."
        ),
        bounty_mind=0,
        tags=[project, "noon-pulse", "pulse"],
        project=project,
        created_by=f"pulse:{project}",
    )
    if not root_id:
        return ""

    logger.info("pulse: posted noon root %s (project=%s)", root_id, project)

    child_id = await _safe_create(
        client,
        title="check-health-of-objectives",
        parent_id=root_id,
        description=(
            f"Walk the open objectives tree for project={project}. "
            f"Flag any claimed-but-stale, any unclaimed past deadline. "
            f"Post a one-line summary as the artifact."
        ),
        bounty_mind=50,
        tags=[project, "noon-pulse", "kind:health-check"],
        capabilities_required=["health-check"],
        project=project,
        created_by=f"pulse:{project}",
    )
    if child_id:
        logger.info(
            "pulse: posted noon child %s (check-health-of-objectives)",
            child_id,
        )

    return root_id


# ---------------------------------------------------------------------------
# Evening pulse — postmortem + harvest-winners
# ---------------------------------------------------------------------------


async def post_evening_pulse(
    project: str,
    *,
    client: AsyncObjectivesClient | None = None,
    workflows: list[dict[str, Any]] | None = None,
    workflows_file: str | None = None,
) -> str:
    """Post the evening postmortem + harvest root for ``project``.

    Evening has two fixed children (``evening-postmortem``,
    ``harvest-winners``). ``workflows`` / ``workflows_file`` are accepted for
    API uniformity with the morning window but are ignored.
    """
    del workflows, workflows_file  # evening is not workflow-driven today
    day = _today_stamp()
    if client is None:
        client = AsyncObjectivesClient()

    root_id = await _safe_create(
        client,
        title=f"{project} evening pulse — {day}",
        description=(
            f"End-of-day reflection pulse for project={project}. "
            f"Summarize completed work, promote winners into the demo bank."
        ),
        bounty_mind=0,
        tags=[project, "evening-pulse", "pulse"],
        project=project,
        created_by=f"pulse:{project}",
    )
    if not root_id:
        return ""

    logger.info("pulse: posted evening root %s (project=%s)", root_id, project)

    postmortem_id = await _safe_create(
        client,
        title="evening-postmortem",
        parent_id=root_id,
        description=f"Summarize today's completed work for {project}.",
        bounty_mind=200,
        tags=[project, "evening-pulse", "kind:postmortem"],
        capabilities_required=["postmortem"],
        project=project,
        created_by=f"pulse:{project}",
    )
    if postmortem_id:
        logger.info(
            "pulse: posted evening child %s (evening-postmortem)",
            postmortem_id,
        )

    harvest_id = await _safe_create(
        client,
        title="harvest-winners",
        parent_id=root_id,
        description=(
            f"Promote today's high-scoring paid objectives for {project} "
            f"into the demo bank for retrieval by future agents."
        ),
        bounty_mind=100,
        tags=[project, "evening-pulse", "kind:harvest-winners"],
        capabilities_required=["harvest-winners"],
        project=project,
        created_by=f"pulse:{project}",
    )
    if harvest_id:
        logger.info(
            "pulse: posted evening child %s (harvest-winners)",
            harvest_id,
        )

    return root_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_WINDOW_DISPATCH = {
    "morning": post_morning_pulse,
    "noon": post_noon_pulse,
    "evening": post_evening_pulse,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sos.services.operations.pulse",
        description="Post a pulse objective tree for a project.",
    )
    p.add_argument(
        "--project",
        required=True,
        help="Project slug. Must match an active tenant.",
    )
    p.add_argument(
        "--window",
        choices=sorted(_WINDOW_DISPATCH.keys()),
        default="morning",
        help="Which daily pulse to post. Default: morning (BC with pre-S6).",
    )
    p.add_argument(
        "--workflows-file",
        default=None,
        help=(
            "Path to a JSON array of workflow dicts. Overrides env "
            "SOS_PULSE_WORKFLOWS_FILE_<PROJECT> and SOS_PULSE_WORKFLOWS_FILE."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = _parse_args(argv)
    fn = _WINDOW_DISPATCH[args.window]
    root_id = asyncio.run(fn(args.project, workflows_file=args.workflows_file))
    if not root_id:
        logger.info(
            "pulse: completed with no root posted (window=%s, degraded mode)",
            args.window,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
