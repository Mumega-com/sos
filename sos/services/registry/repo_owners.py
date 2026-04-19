"""Repo-owner lookup for cross-repo coordination.

When an SOS-side session files a GitHub issue on a sibling repo (Inkwell,
mumega-edge, per-instance forks, etc.), it should ping the owning agent
via the internal bus so a human doesn't have to play postman. This module
is the map from ``<org>/<repo>`` to the SOS agent_id that owns it.

Slice 1 of INFRA-001. Later slices add a GH webhook → bus bridge and an
external_ref field on SquadTask so completion signals flow back.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, TypedDict

_REGISTRY_PATH = Path(__file__).parent / "repo_owners.json"


class RepoOwner(TypedDict):
    agent: str
    role: str


def _load() -> dict:
    return json.loads(_REGISTRY_PATH.read_text())


def owner_for(repo: str) -> str:
    """Return the agent_id that owns a given ``<org>/<repo>``.

    Falls back to the registry's ``_default_agent`` when no explicit
    entry exists so callers never get a KeyError at the ping site.
    """
    data = _load()
    entry = data.get("owners", {}).get(repo)
    if entry is None:
        return data["_default_agent"]
    return entry["agent"]


def owner_entry(repo: str) -> RepoOwner | None:
    """Return the full ``{agent, role}`` entry, or ``None`` when not listed.

    Use this when you need the role context (for the bus ping body) rather
    than just the agent_id.
    """
    data = _load()
    entry = data.get("owners", {}).get(repo)
    if entry is None:
        return None
    return {"agent": entry["agent"], "role": entry["role"]}


def all_repos() -> Iterator[tuple[str, RepoOwner]]:
    """Yield every ``(repo, {agent, role})`` pair in the registry."""
    data = _load()
    for repo, entry in data.get("owners", {}).items():
        yield repo, {"agent": entry["agent"], "role": entry["role"]}
