"""
Squad Connector Interface — external task system integrations.

Connectors bridge the squad service with external task trackers
(GitHub, ClickUp, Notion, Linear) and our internal Mirror API.

Usage:
    registry = ConnectorRegistry()
    registry.register(MirrorConnector())
    registry.register(GitHubConnector(token="ghp_..."))

    connector = registry.get(ConnectorType.MIRROR)
    tasks = await connector.import_tasks("my-project")
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from sos.contracts.squad import ConnectorType, ExternalRef, SyncReport

logger = logging.getLogger(__name__)


# ── Base ──────────────────────────────────────────────────────────────────────

class BaseConnector:
    """Abstract connector interface — all external systems implement this."""

    connector_type: ConnectorType

    async def import_tasks(self, source_ref: str) -> list[dict[str, Any]]:
        """Pull tasks from external system into squad format.

        Args:
            source_ref: System-specific identifier (project slug, repo name, etc.)

        Returns:
            List of task dicts shaped for SquadTask ingestion.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} has not implemented import_tasks"
        )

    async def export_result(self, task: dict[str, Any], result: dict[str, Any]) -> ExternalRef:
        """Push task result to external system.

        Args:
            task: The squad task dict (must include 'id' and 'external_ref' if updating).
            result: The execution result payload.

        Returns:
            ExternalRef pointing to the created/updated record in the external system.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} has not implemented export_result"
        )

    async def sync_status(self, task_id: str, status: str) -> bool:
        """Update task status in external system.

        Args:
            task_id: The external system's task ID.
            status: New status string (should map to TaskStatus values).

        Returns:
            True on success, False on failure.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} has not implemented sync_status"
        )

    def _now_iso(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat()


# ── Mirror ────────────────────────────────────────────────────────────────────

class MirrorConnector(BaseConnector):
    """Connects to our Mirror API for task persistence.

    Mirror is the internal source of truth for memory and tasks.
    Endpoints used:
        GET  /tasks?project={project}   — list tasks
        PUT  /tasks/{id}                — update task / write result
    """

    connector_type = ConnectorType.MIRROR

    def __init__(
        self,
        mirror_url: str | None = None,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = (mirror_url or os.environ.get("MIRROR_URL", "http://localhost:8844")).rstrip("/")
        self._token = token or os.environ.get("MIRROR_TOKEN", "")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def import_tasks(self, source_ref: str) -> list[dict[str, Any]]:
        """Pull tasks from Mirror API for a given project slug.

        Args:
            source_ref: Project slug (e.g. "gaf", "dnu").
        """
        url = f"{self._base_url}/tasks"
        params = {"project": source_ref}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(url, headers=self._headers(), params=params)
                response.raise_for_status()
                data = response.json()
                tasks: list[dict[str, Any]] = data if isinstance(data, list) else data.get("tasks", [])
                logger.info(
                    "mirror.import_tasks project=%s count=%d", source_ref, len(tasks)
                )
                return tasks
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "mirror.import_tasks HTTP %d for project=%s: %s",
                    exc.response.status_code,
                    source_ref,
                    exc.response.text,
                )
                return []
            except httpx.RequestError as exc:
                logger.error("mirror.import_tasks connection error: %s", exc)
                return []

    async def export_result(self, task: dict[str, Any], result: dict[str, Any]) -> ExternalRef:
        """Write task result back to Mirror.

        Args:
            task: Squad task dict — must contain 'id'.
            result: Execution result payload.
        """
        task_id: str = task["id"]
        url = f"{self._base_url}/tasks/{task_id}"
        payload: dict[str, Any] = {
            "result": result,
            "status": "done",
            "updated_at": self._now_iso(),
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.put(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                data = response.json()
                external_url: str = data.get("url", f"{self._base_url}/tasks/{task_id}")
                logger.info("mirror.export_result task_id=%s ok", task_id)
                return ExternalRef(
                    connector=ConnectorType.MIRROR,
                    external_id=task_id,
                    url=external_url,
                    synced_at=self._now_iso(),
                )
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "mirror.export_result HTTP %d task_id=%s: %s",
                    exc.response.status_code,
                    task_id,
                    exc.response.text,
                )
                raise
            except httpx.RequestError as exc:
                logger.error("mirror.export_result connection error task_id=%s: %s", task_id, exc)
                raise

    async def sync_status(self, task_id: str, status: str) -> bool:
        """Update task status in Mirror.

        Args:
            task_id: Mirror task ID.
            status: New status value (TaskStatus string).
        """
        url = f"{self._base_url}/tasks/{task_id}"
        payload: dict[str, Any] = {
            "status": status,
            "updated_at": self._now_iso(),
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.put(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                logger.info("mirror.sync_status task_id=%s status=%s ok", task_id, status)
                return True
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "mirror.sync_status HTTP %d task_id=%s: %s",
                    exc.response.status_code,
                    task_id,
                    exc.response.text,
                )
                return False
            except httpx.RequestError as exc:
                logger.error("mirror.sync_status connection error task_id=%s: %s", task_id, exc)
                return False


# ── GitHub ────────────────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"

# Maps TaskStatus values to GitHub issue states
_GITHUB_OPEN_STATUSES = {"backlog", "queued", "claimed", "in_progress", "review", "blocked"}
_GITHUB_CLOSED_STATUSES = {"done", "canceled", "failed"}


class GitHubConnector(BaseConnector):
    """Connects to GitHub Issues for task sync.

    Reads GITHUB_TOKEN from env if not provided explicitly.

    import_tasks:   lists open issues from a repo (owner/repo format)
    export_result:  posts a comment with the execution result
    sync_status:    closes or reopens the issue based on status
    """

    connector_type = ConnectorType.GITHUB

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        resolved_token = token or os.environ.get("GITHUB_TOKEN", "")
        if not resolved_token:
            logger.warning(
                "GitHubConnector: no token provided and GITHUB_TOKEN not set — "
                "API calls will be unauthenticated and rate-limited"
            )
        self._token = resolved_token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _issue_to_task(self, issue: dict[str, Any], repo: str) -> dict[str, Any]:
        """Normalise a GitHub issue dict to squad task shape."""
        labels: list[str] = [lbl["name"] for lbl in issue.get("labels", [])]
        priority = "medium"
        for lbl in labels:
            lbl_lower = lbl.lower()
            if lbl_lower in {"critical", "high", "medium", "low"}:
                priority = lbl_lower
                break

        return {
            "id": str(issue["number"]),
            "title": issue["title"],
            "description": issue.get("body") or "",
            "status": "backlog",
            "priority": priority,
            "labels": labels,
            "external_ref": str(issue["number"]),
            "project": repo,
            "inputs": {
                "github_url": issue.get("html_url", ""),
                "github_repo": repo,
                "github_issue_number": issue["number"],
            },
            "created_at": issue.get("created_at", ""),
            "updated_at": issue.get("updated_at", ""),
        }

    async def import_tasks(self, source_ref: str) -> list[dict[str, Any]]:
        """List open issues from a GitHub repo.

        Args:
            source_ref: Repository in 'owner/repo' format (e.g. 'mumega/sovereign').
        """
        url = f"{_GITHUB_API}/repos/{source_ref}/issues"
        params = {"state": "open", "per_page": 100}
        all_tasks: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                while url:
                    response = await client.get(url, headers=self._headers(), params=params)
                    response.raise_for_status()
                    issues: list[dict[str, Any]] = response.json()
                    # GitHub includes PRs in /issues — filter them out
                    issues = [i for i in issues if "pull_request" not in i]
                    all_tasks.extend(self._issue_to_task(issue, source_ref) for issue in issues)
                    # Follow Link header for pagination
                    link_header: str = response.headers.get("Link", "")
                    next_url: str | None = None
                    for part in link_header.split(","):
                        if 'rel="next"' in part:
                            next_url = part.split(";")[0].strip().strip("<>")
                            break
                    url = next_url  # type: ignore[assignment]
                    params = {}  # pagination params embedded in next URL

                logger.info(
                    "github.import_tasks repo=%s count=%d", source_ref, len(all_tasks)
                )
                return all_tasks
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "github.import_tasks HTTP %d repo=%s: %s",
                    exc.response.status_code,
                    source_ref,
                    exc.response.text,
                )
                return []
            except httpx.RequestError as exc:
                logger.error("github.import_tasks connection error: %s", exc)
                return []

    async def export_result(self, task: dict[str, Any], result: dict[str, Any]) -> ExternalRef:
        """Post a comment on a GitHub issue with the execution result.

        Args:
            task: Squad task dict — must contain 'inputs.github_repo' and 'id' (issue number).
            result: Execution result payload.
        """
        repo: str = task.get("inputs", {}).get("github_repo", "")
        issue_number: str | int = task.get("inputs", {}).get("github_issue_number") or task["id"]

        if not repo:
            raise ValueError(
                "GitHubConnector.export_result: task must have inputs.github_repo set"
            )

        url = f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
        summary: str = result.get("summary", "")
        output_md: str = _dict_to_md_table(result.get("output", {}))
        body = (
            "### Squad Execution Result\n\n"
            + (f"{summary}\n\n" if summary else "")
            + output_md
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    url, headers=self._headers(), json={"body": body}
                )
                response.raise_for_status()
                data = response.json()
                comment_url: str = data.get("html_url", "")
                logger.info(
                    "github.export_result repo=%s issue=%s comment=%s",
                    repo,
                    issue_number,
                    comment_url,
                )
                return ExternalRef(
                    connector=ConnectorType.GITHUB,
                    external_id=str(issue_number),
                    url=comment_url,
                    synced_at=self._now_iso(),
                )
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "github.export_result HTTP %d repo=%s issue=%s: %s",
                    exc.response.status_code,
                    repo,
                    issue_number,
                    exc.response.text,
                )
                raise
            except httpx.RequestError as exc:
                logger.error(
                    "github.export_result connection error repo=%s issue=%s: %s",
                    repo,
                    issue_number,
                    exc,
                )
                raise

    async def sync_status(self, task_id: str, status: str) -> bool:
        """Close or reopen a GitHub issue based on squad task status.

        Args:
            task_id: Must be in 'owner/repo#number' format (e.g. 'mumega/gaf#42'),
                     or just the issue number if repo is pre-configured.
            status: TaskStatus string value.
        """
        # Support 'owner/repo#number' format
        if "#" in task_id:
            repo, number_str = task_id.rsplit("#", 1)
        else:
            logger.error(
                "github.sync_status: task_id must be 'owner/repo#number', got '%s'",
                task_id,
            )
            return False

        github_state = "closed" if status in _GITHUB_CLOSED_STATUSES else "open"
        url = f"{_GITHUB_API}/repos/{repo}/issues/{number_str}"
        payload: dict[str, Any] = {"state": github_state}

        # Attach a state_reason on close so GitHub shows it correctly
        if status == "done":
            payload["state_reason"] = "completed"
        elif status in {"canceled", "failed"}:
            payload["state_reason"] = "not_planned"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.patch(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                logger.info(
                    "github.sync_status repo=%s issue=%s status=%s -> github_state=%s ok",
                    repo,
                    number_str,
                    status,
                    github_state,
                )
                return True
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "github.sync_status HTTP %d repo=%s issue=%s: %s",
                    exc.response.status_code,
                    repo,
                    number_str,
                    exc.response.text,
                )
                return False
            except httpx.RequestError as exc:
                logger.error(
                    "github.sync_status connection error repo=%s issue=%s: %s",
                    repo,
                    number_str,
                    exc,
                )
                return False


# ── ClickUp ───────────────────────────────────────────────────────────────────

class ClickUpConnector(BaseConnector):
    """Stub for future ClickUp integration.

    TODO: implement using ClickUp REST API v2
      - API ref: https://clickup.com/api/
      - Auth: CLICKUP_API_TOKEN env var
      - import_tasks: GET /list/{list_id}/task
      - export_result: POST /task/{task_id}/comment
      - sync_status: PUT /task/{task_id} {"status": <status_name>}
    """

    connector_type = ConnectorType.CLICKUP

    async def import_tasks(self, source_ref: str) -> list[dict[str, Any]]:
        # TODO: source_ref = ClickUp list ID
        raise NotImplementedError(
            "ClickUpConnector.import_tasks is not yet implemented. "
            "Set CLICKUP_API_TOKEN and implement GET /list/{list_id}/task"
        )

    async def export_result(self, task: dict[str, Any], result: dict[str, Any]) -> ExternalRef:
        # TODO: POST /task/{task_id}/comment with result summary
        raise NotImplementedError(
            "ClickUpConnector.export_result is not yet implemented. "
            "Implement POST /task/{task_id}/comment"
        )

    async def sync_status(self, task_id: str, status: str) -> bool:
        # TODO: PUT /task/{task_id} {"status": <clickup_status_name>}
        # Note: ClickUp statuses are list-specific strings, not enums — needs a mapping table
        raise NotImplementedError(
            "ClickUpConnector.sync_status is not yet implemented. "
            "Implement PUT /task/{task_id} with a status mapping table"
        )


# ── Notion ────────────────────────────────────────────────────────────────────

class NotionConnector(BaseConnector):
    """Stub for future Notion integration.

    TODO: implement using Notion API v1
      - API ref: https://developers.notion.com/reference/intro
      - Auth: NOTION_API_KEY env var (internal integration token)
      - import_tasks: POST /databases/{database_id}/query
      - export_result: PATCH /pages/{page_id} with result properties
      - sync_status: PATCH /pages/{page_id} with status select property
    """

    connector_type = ConnectorType.NOTION

    async def import_tasks(self, source_ref: str) -> list[dict[str, Any]]:
        # TODO: source_ref = Notion database ID
        raise NotImplementedError(
            "NotionConnector.import_tasks is not yet implemented. "
            "Set NOTION_API_KEY and implement POST /databases/{id}/query"
        )

    async def export_result(self, task: dict[str, Any], result: dict[str, Any]) -> ExternalRef:
        # TODO: PATCH /pages/{page_id} — update result-related properties
        raise NotImplementedError(
            "NotionConnector.export_result is not yet implemented. "
            "Implement PATCH /pages/{page_id} with result properties"
        )

    async def sync_status(self, task_id: str, status: str) -> bool:
        # TODO: PATCH /pages/{task_id} {"properties": {"Status": {"select": {"name": ...}}}}
        raise NotImplementedError(
            "NotionConnector.sync_status is not yet implemented. "
            "Implement PATCH /pages/{task_id} with Status select property"
        )


# ── Linear ────────────────────────────────────────────────────────────────────

class LinearConnector(BaseConnector):
    """Stub for future Linear integration.

    TODO: implement using Linear GraphQL API
      - API ref: https://developers.linear.app/docs/graphql/working-with-the-graphql-api
      - Auth: LINEAR_API_KEY env var
      - import_tasks: issues(filter: {team: {key: {eq: source_ref}}}) query
      - export_result: createComment mutation on issue
      - sync_status: updateIssue mutation with stateId lookup
    """

    connector_type = ConnectorType.LINEAR

    async def import_tasks(self, source_ref: str) -> list[dict[str, Any]]:
        # TODO: source_ref = Linear team key (e.g. "ENG")
        raise NotImplementedError(
            "LinearConnector.import_tasks is not yet implemented. "
            "Set LINEAR_API_KEY and implement the issues GraphQL query"
        )

    async def export_result(self, task: dict[str, Any], result: dict[str, Any]) -> ExternalRef:
        # TODO: createComment mutation — issue ID from task['external_ref']
        raise NotImplementedError(
            "LinearConnector.export_result is not yet implemented. "
            "Implement the createComment GraphQL mutation"
        )

    async def sync_status(self, task_id: str, status: str) -> bool:
        # TODO: updateIssue mutation — requires resolving status name to Linear stateId
        raise NotImplementedError(
            "LinearConnector.sync_status is not yet implemented. "
            "Implement updateIssue mutation with state resolution"
        )


# ── Registry ──────────────────────────────────────────────────────────────────

class ConnectorRegistry:
    """Central registry for all squad connectors.

    Example:
        registry = ConnectorRegistry()
        registry.register(MirrorConnector())
        registry.register(GitHubConnector())

        connector = registry.get(ConnectorType.GITHUB)
        tasks = await connector.import_tasks("mumega/gaf")
    """

    def __init__(self) -> None:
        self._connectors: dict[ConnectorType, BaseConnector] = {}

    def register(self, connector: BaseConnector) -> None:
        """Register a connector instance, keyed by its connector_type."""
        self._connectors[connector.connector_type] = connector
        logger.debug("connector.registered type=%s", connector.connector_type)

    def get(self, connector_type: ConnectorType) -> BaseConnector:
        """Return the registered connector for the given type.

        Raises:
            KeyError: if no connector is registered for that type.
        """
        if connector_type not in self._connectors:
            available = [ct.value for ct in self._connectors]
            raise KeyError(
                f"No connector registered for {connector_type.value!r}. "
                f"Available: {available}"
            )
        return self._connectors[connector_type]

    def list_available(self) -> list[ConnectorType]:
        """Return all registered connector types."""
        return list(self._connectors.keys())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dict_to_md_table(data: dict[str, Any]) -> str:
    """Convert a flat dict to a Markdown table for GitHub comments."""
    if not data:
        return ""
    rows = [f"| {k} | {v} |" for k, v in data.items()]
    header = "| Key | Value |\n|-----|-------|"
    return header + "\n" + "\n".join(rows)
