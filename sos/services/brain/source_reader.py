"""source_reader — project source manifest loader + active-project registry.

Sprint 007 G64b: structural foundation for per-project brain routing.

Public API:
    read_sources(project_slug)      → SourceManifest (raises on missing/malformed)
    list_active_projects()          → frozenset[str]
    is_active(project_slug)         → bool

Exceptions:
    SourceNotFoundError             — SOURCES.md file not found for project
    SourceManifestParseError        — SOURCES.md or active_projects.json malformed
    MissingProjectIdError           — dispatch message lacks project_id

Parser purity (BLOCK-4): read_sources MUST NOT call eval, exec, compile,
__import__, or open() on any path derived from SOURCES.md content. Parse is
pure-string operation on the file at the known path only.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("sos.brain.source_reader")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOS_ROOT = Path("/home/mumega/SOS")
_PROJECTS_DIR = _SOS_ROOT / "projects"
_ACTIVE_PROJECTS_PATH = _SOS_ROOT / "sos" / "brain" / "active_projects.json"

_SAFE_DEFAULT_PROJECTS: frozenset[str] = frozenset({"mumega", "reserve"})

# The four canonical sections in SOURCES.md
_SECTIONS = ("motor", "sensor", "memory", "signal")
_SECTION_PATTERN = re.compile(r"^##\s+(motor|sensor|memory|signal)\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SourceNotFoundError(FileNotFoundError):
    """SOURCES.md not found for project slug."""


class SourceManifestParseError(ValueError):
    """SOURCES.md or active_projects.json is malformed."""


class MissingProjectIdError(ValueError):
    """Dispatch message lacks project_id field."""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceManifest:
    """Parsed SOURCES.md — four lists of source entries."""

    project_slug: str
    motor: list[str] = field(default_factory=list)
    sensor: list[str] = field(default_factory=list)
    memory: list[str] = field(default_factory=list)
    signal: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# read_sources — parse SOURCES.md (BLOCK-4: pure string parse, no eval/exec)
# ---------------------------------------------------------------------------


def read_sources(project_slug: str, *, base_dir: Path | None = None) -> SourceManifest:
    """Read and parse ``projects/<slug>/SOURCES.md``.

    Raises SourceNotFoundError if the file does not exist.
    Raises SourceManifestParseError if the structure is malformed
    (missing required sections).

    The ``base_dir`` parameter is for testing only; production uses
    ``_PROJECTS_DIR``.
    """
    root = base_dir if base_dir is not None else _PROJECTS_DIR
    sources_path = root / project_slug / "SOURCES.md"

    if not sources_path.exists():
        raise SourceNotFoundError(
            f"SOURCES.md not found for project '{project_slug}' "
            f"at {sources_path}"
        )

    text = sources_path.read_text(encoding="utf-8")
    return _parse_sources_md(project_slug, text)


def _parse_sources_md(project_slug: str, text: str) -> SourceManifest:
    """Pure-string parser for SOURCES.md content.

    Expected structure:
        ## motor
        - entry1
        - entry2

        ## sensor
        ...

    All four sections (motor, sensor, memory, signal) must be present.
    Content lines are stored as-is (inert strings — never interpreted).
    """
    sections_found: dict[str, list[str]] = {}
    current_section: str | None = None

    for line_num, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        # Check for section header
        match = _SECTION_PATTERN.match(line)
        if match:
            section_name = match.group(1).lower()
            if section_name in sections_found:
                raise SourceManifestParseError(
                    f"Duplicate section '## {section_name}' at line {line_num} "
                    f"in {project_slug}/SOURCES.md"
                )
            sections_found[section_name] = []
            current_section = section_name
            continue

        # Accumulate non-empty content lines under current section
        if current_section is not None and line and not line.startswith("#"):
            # Strip leading "- " for list items, keep content as inert string
            entry = line.lstrip("- ").strip()
            if entry:
                sections_found[current_section].append(entry)

    # Validate all four sections present
    missing = [s for s in _SECTIONS if s not in sections_found]
    if missing:
        raise SourceManifestParseError(
            f"Missing required section(s) {missing} in {project_slug}/SOURCES.md. "
            f"Found: {list(sections_found.keys())}"
        )

    return SourceManifest(
        project_slug=project_slug,
        motor=sections_found["motor"],
        sensor=sections_found["sensor"],
        memory=sections_found["memory"],
        signal=sections_found["signal"],
    )


# ---------------------------------------------------------------------------
# list_active_projects / is_active — active_projects.json registry
# ---------------------------------------------------------------------------


def list_active_projects(*, config_path: Path | None = None) -> frozenset[str]:
    """Return the set of active project slugs.

    Reads ``sos/brain/active_projects.json`` on every call (no caching —
    deactivating a project takes effect without brain restart, per WARN-2).

    If the file is missing: returns safe default ``{'mumega', 'reserve'}``
    + logs WARN (graceful boot).

    If the file exists but is invalid JSON or fails schema validation
    (missing ``active`` key, ``active`` is not a list of strings):
    raises ``SourceManifestParseError``. Corrupt config = loud brain-boot
    failure. (BLOCK-2)
    """
    path = config_path if config_path is not None else _ACTIVE_PROJECTS_PATH

    if not path.exists():
        logger.warning(
            "active_projects.json not found at %s; using safe default %s",
            path,
            _SAFE_DEFAULT_PROJECTS,
        )
        return _SAFE_DEFAULT_PROJECTS

    raw = path.read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceManifestParseError(
            f"active_projects.json is invalid JSON at {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise SourceManifestParseError(
            f"active_projects.json must be a JSON object, got {type(data).__name__}"
        )

    active = data.get("active")
    if active is None:
        raise SourceManifestParseError(
            "active_projects.json missing required 'active' key"
        )

    if not isinstance(active, list):
        raise SourceManifestParseError(
            f"active_projects.json 'active' must be a list, got {type(active).__name__}"
        )

    for i, item in enumerate(active):
        if not isinstance(item, str):
            raise SourceManifestParseError(
                f"active_projects.json 'active[{i}]' must be a string, got {type(item).__name__}"
            )

    return frozenset(active)


def is_active(project_slug: str, *, config_path: Path | None = None) -> bool:
    """Check if a project slug is in the active set.

    Reads per-call (no cache). See ``list_active_projects`` for error
    semantics.
    """
    return project_slug in list_active_projects(config_path=config_path)


# ---------------------------------------------------------------------------
# extract_project_from_stream — stream-key authoritative source (BLOCK-1)
# ---------------------------------------------------------------------------


def extract_project_from_stream(stream_key: str) -> str | None:
    """Extract the project/squad suffix from a squad stream key.

    ``sos:stream:global:squad:<suffix>`` → ``<suffix>``

    Returns None for non-squad streams (registry, agent, etc.) — those
    don't carry project context and are not subject to the project gate.
    """
    prefix = "sos:stream:global:squad:"
    if stream_key.startswith(prefix):
        suffix = stream_key[len(prefix):]
        return suffix if suffix else None
    return None
