"""
Project registry for hub routing and worker resurrection.

The registry lives in ~/.sos/projects.json so hub agents can share a single
project map independent of any one CLI session. It answers:

- which repo a project lives in
- which model/agent family should handle that project by default
- whether that project's worker context should be kept warm or treated as cold
- which context files should be loaded when a worker is resurrected
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


PROJECTS_FILE = Path.home() / ".sos" / "projects.json"


@dataclass(frozen=True)
class ProjectDef:
    name: str
    repo_path: str
    preferred_agent: str
    preferred_model: str
    warm_policy: str
    context_files: tuple[str, ...]
    notes: str = ""


DEFAULT_PROJECTS: dict[str, dict[str, object]] = {
    "workspace": {
        "repo_path": str(Path.home()),
        "preferred_agent": "codex",
        "preferred_model": "gpt-5.4",
        "warm_policy": "warm",
        "context_files": ["SOUL.md", "USER.md", "MEMORY.md"],
        "notes": "Root coordinator context at /home/mumega.",
    },
    "sos": {
        "repo_path": str(Path.home() / "SOS"),
        "preferred_agent": "codex",
        "preferred_model": "gpt-5.4",
        "warm_policy": "warm",
        "context_files": ["AGENTS.md", "CLAUDE.md", "GEMINI.md"],
        "notes": "Control plane, lifecycle, routing, and infra work.",
    },
    "gaf": {
        "repo_path": str(Path.home() / "gaf-app"),
        "preferred_agent": "claude-code",
        "preferred_model": "claude-sonnet",
        "warm_policy": "cold",
        "context_files": ["docs/startup/README.md", "docs/startup/pipeline-workflows.md"],
        "notes": "Grant and funding product work.",
    },
    "dentalnearyou": {
        "repo_path": str(Path.home() / "dentalnearyou"),
        "preferred_agent": "claude-code",
        "preferred_model": "claude-sonnet",
        "warm_policy": "cold",
        "context_files": ["README.md"],
        "notes": "Large customer-facing product repo with lighter tests.",
    },
    "mumega-site": {
        "repo_path": str(Path.home() / "mumega-site"),
        "preferred_agent": "claude-code",
        "preferred_model": "claude-sonnet",
        "warm_policy": "cold",
        "context_files": ["README.md"],
        "notes": "Public site and CMS work.",
    },
    "mirror": {
        "repo_path": str(Path.home() / "mirror"),
        "preferred_agent": "codex",
        "preferred_model": "gpt-5.4",
        "warm_policy": "cold",
        "context_files": ["README.md"],
        "notes": "State and memory substrate.",
    },
    "torivers": {
        "repo_path": str(Path.home() / "torivers-staging-dev"),
        "preferred_agent": "claude-code",
        "preferred_model": "claude-sonnet",
        "warm_policy": "cold",
        "context_files": ["README.md"],
        "notes": "Project-local worker only.",
    },
    "therealmofpatterns": {
        "repo_path": str(Path.home() / "therealmofpatterns"),
        "preferred_agent": "gemini",
        "preferred_model": "gemini-2.5-flash",
        "warm_policy": "cold",
        "context_files": ["README.md"],
        "notes": "Research and content-heavy lane.",
    },
}


def _default_payload() -> dict[str, object]:
    return {
        "_doc": "SOS project registry — project -> repo path -> preferred agent/model -> warm/cold -> context files.",
        "_version": 1,
        "projects": DEFAULT_PROJECTS,
    }


def _ensure_registry_file() -> None:
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_FILE.exists():
        PROJECTS_FILE.write_text(json.dumps(_default_payload(), indent=2) + "\n")


def _raw_registry() -> dict[str, object]:
    _ensure_registry_file()
    try:
        raw = json.loads(PROJECTS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        raw = _default_payload()
    if "projects" not in raw or not isinstance(raw["projects"], dict):
        raw = _default_payload()
    return raw


def _normalize_context_files(repo_path: Path, context_files: object) -> tuple[str, ...]:
    if not isinstance(context_files, list):
        return ()
    result: list[str] = []
    for item in context_files:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = repo_path / item
        result.append(str(path))
    return tuple(result)


def _normalize_project(name: str, raw: object) -> ProjectDef:
    if not isinstance(raw, dict):
        raw = {}
    repo_path = Path(str(raw.get("repo_path", Path.home()))).expanduser()
    preferred_agent = str(raw.get("preferred_agent", "codex"))
    preferred_model = str(raw.get("preferred_model", "gpt-5.4"))
    warm_policy = str(raw.get("warm_policy", "cold")).lower()
    if warm_policy not in {"warm", "cold"}:
        warm_policy = "cold"
    notes = str(raw.get("notes", ""))
    context_files = _normalize_context_files(repo_path, raw.get("context_files", []))
    return ProjectDef(
        name=name,
        repo_path=str(repo_path),
        preferred_agent=preferred_agent,
        preferred_model=preferred_model,
        warm_policy=warm_policy,
        context_files=context_files,
        notes=notes,
    )


def get_all_projects() -> dict[str, ProjectDef]:
    raw = _raw_registry()["projects"]
    assert isinstance(raw, dict)
    return {name: _normalize_project(name, project) for name, project in raw.items()}


def get_project(name: str) -> ProjectDef | None:
    return get_all_projects().get(name)


def get_context_files(name: str) -> tuple[str, ...]:
    project = get_project(name)
    return project.context_files if project else ()


def resolve_project_for_path(path: str) -> ProjectDef | None:
    target = Path(path).expanduser().resolve()
    matches: list[tuple[int, ProjectDef]] = []
    for project in get_all_projects().values():
        repo = Path(project.repo_path).expanduser().resolve()
        try:
            target.relative_to(repo)
        except ValueError:
            continue
        matches.append((len(str(repo)), project))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]
