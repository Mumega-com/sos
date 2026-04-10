"""
Execution Proof — Gap #6 from HARNESS-GAPS.md

Verifies that agent actions actually happened. Called after task completion.
Stores verification result with the task in Squad Service.

Verification types:
  - url_check: HTTP GET, check status 200 + optional content match
  - task_exists: Query Squad Service for task ID
  - file_exists: Check file path on disk
  - git_check: Verify commit exists in repo

Usage:
  from sos.kernel.verification import verify_action
  result = await verify_action("url_check", "https://viamar.ca/pricing", match="testimonials")
  # {"verified": True, "method": "url_check", "evidence": {...}}

Integrated with output capture: when VERIFY: line is parsed,
this module runs the verification and stores proof in Mirror.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests as http_requests

logger = logging.getLogger("verification")

MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
SQUAD_URL = os.environ.get("SQUAD_URL", "http://127.0.0.1:8060")


async def verify_action(
    method: str,
    target: str,
    match: str = "",
    task_id: str = "",
    agent: str = "",
) -> dict:
    """Verify an action was executed.

    Args:
        method: Verification type (url_check, task_exists, file_exists, git_check)
        target: What to check (URL, task_id, file path, commit hash)
        match: Optional content to match in response
        task_id: Associated task ID (for storing result)
        agent: Agent that performed the action

    Returns:
        {"verified": bool, "method": str, "evidence": dict, "timestamp": str}
    """
    result = {
        "verified": False,
        "method": method,
        "target": target,
        "agent": agent,
        "task_id": task_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence": {},
    }

    if method == "url_check":
        result = _verify_url(result, target, match)
    elif method == "task_exists":
        result = _verify_task(result, target)
    elif method == "file_exists":
        result = _verify_file(result, target, match)
    elif method == "git_check":
        result = _verify_git(result, target)
    else:
        result["evidence"]["error"] = f"Unknown verification method: {method}"

    # Store verification result
    _store_verification(result)

    return result


def _verify_url(result: dict, url: str, match: str) -> dict:
    """Verify a URL is accessible and optionally contains expected content."""
    try:
        resp = http_requests.get(url, timeout=15, allow_redirects=True)
        result["evidence"]["status_code"] = resp.status_code
        result["evidence"]["content_length"] = len(resp.content)
        result["evidence"]["content_type"] = resp.headers.get("content-type", "")

        if resp.status_code == 200:
            result["verified"] = True
            if match:
                content = resp.text.lower()
                result["evidence"]["match_found"] = match.lower() in content
                if not result["evidence"]["match_found"]:
                    result["verified"] = False
                    result["evidence"]["match_expected"] = match
        else:
            result["evidence"]["error"] = f"HTTP {resp.status_code}"

    except Exception as e:
        result["evidence"]["error"] = str(e)[:200]

    return result


def _verify_task(result: dict, task_id: str) -> dict:
    """Verify a task exists in Squad Service."""
    try:
        resp = http_requests.get(f"{SQUAD_URL}/tasks/{task_id}", timeout=5)
        if resp.status_code == 200:
            task = resp.json()
            result["verified"] = True
            result["evidence"]["task_status"] = task.get("status", "unknown")
            result["evidence"]["task_title"] = task.get("title", "")
        else:
            result["evidence"]["error"] = f"Task not found: HTTP {resp.status_code}"
    except Exception as e:
        result["evidence"]["error"] = str(e)[:200]

    return result


def _verify_file(result: dict, path: str, match: str) -> dict:
    """Verify a file exists and optionally contains expected content."""
    file_path = Path(path).expanduser()
    if file_path.exists():
        result["verified"] = True
        result["evidence"]["size"] = file_path.stat().st_size
        result["evidence"]["modified"] = datetime.fromtimestamp(
            file_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()

        if match and file_path.is_file():
            try:
                content = file_path.read_text()
                result["evidence"]["match_found"] = match in content
                if not result["evidence"]["match_found"]:
                    result["verified"] = False
            except Exception:
                pass
    else:
        result["evidence"]["error"] = "File not found"

    return result


def _verify_git(result: dict, ref: str) -> dict:
    """Verify a git commit/branch/tag exists."""
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", "-1", ref],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            result["verified"] = True
            result["evidence"]["commit"] = proc.stdout.strip()
        else:
            result["evidence"]["error"] = proc.stderr.strip()[:100]
    except Exception as e:
        result["evidence"]["error"] = str(e)[:200]

    return result


def parse_verify_line(verify_text: str) -> tuple[str, str, str]:
    """Parse a VERIFY: line into (method, target, match).

    Examples:
        "Check https://viamar.ca/pricing" → ("url_check", "https://viamar.ca/pricing", "")
        "Check https://viamar.ca/pricing — hero should show new copy"
            → ("url_check", "https://viamar.ca/pricing", "new copy")
        "File exists: /home/mumega/output.json" → ("file_exists", "/home/mumega/output.json", "")
        "git commit abc123" → ("git_check", "abc123", "")
    """
    text = verify_text.strip()

    # URL detection
    if "http://" in text or "https://" in text:
        # Extract URL
        parts = text.split()
        url = ""
        for part in parts:
            if part.startswith("http://") or part.startswith("https://"):
                url = part.rstrip(",;—")
                break

        # Extract match hint after — or "should"
        match = ""
        if "—" in text:
            match = text.split("—", 1)[1].strip()
        elif "should" in text.lower():
            match = text.lower().split("should", 1)[1].strip()

        return ("url_check", url, match)

    # File check
    if text.lower().startswith("file"):
        path = text.split(":", 1)[1].strip() if ":" in text else text.split()[-1]
        return ("file_exists", path, "")

    # Git check
    if "git" in text.lower() or "commit" in text.lower():
        ref = text.split()[-1]
        return ("git_check", ref, "")

    # Task check
    if "task" in text.lower():
        task_id = text.split()[-1]
        return ("task_exists", task_id, "")

    # Default: treat as URL if starts with http
    return ("url_check", text, "")


def _store_verification(result: dict) -> None:
    """Store verification result in Mirror and with the task."""
    # Store in Mirror
    try:
        text = (
            f"VERIFICATION: {result['method']} on {result['target']} — "
            f"{'PASSED' if result['verified'] else 'FAILED'}. "
            f"Agent: {result['agent']}. Evidence: {json.dumps(result['evidence'])[:300]}"
        )
        http_requests.post(
            f"{MIRROR_URL}/store",
            json={"text": text, "agent": result["agent"], "tags": ["verification"]},
            timeout=5,
        )
    except Exception:
        pass

    # Update task result with verification
    if result.get("task_id"):
        try:
            # Get current task
            resp = http_requests.get(f"{SQUAD_URL}/tasks/{result['task_id']}", timeout=5)
            if resp.status_code == 200:
                task = resp.json()
                current_result = task.get("result", {})
                if isinstance(current_result, str):
                    current_result = {"summary": current_result}
                current_result["verification"] = {
                    "verified": result["verified"],
                    "method": result["method"],
                    "evidence": result["evidence"],
                    "timestamp": result["timestamp"],
                }
                # Update task with verification
                http_requests.put(
                    f"{SQUAD_URL}/tasks/{result['task_id']}",
                    json={"result": current_result},
                    timeout=5,
                )
        except Exception:
            pass

    logger.info(
        f"Verification {result['method']}: "
        f"{'PASS' if result['verified'] else 'FAIL'} — {result['target'][:80]}"
    )
