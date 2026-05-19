"""Unit tests for repo_owners — INFRA-001 slice 1.

Pins the registry shape and the fallback behaviour so future edits don't
silently change who gets pinged when an SOS session files a cross-repo
GH issue.
"""
from __future__ import annotations

import pytest

from sos.services.registry.repo_owners import (
    all_repos,
    owner_entry,
    owner_for,
)


def test_owner_for_known_repo_returns_agent():
    assert owner_for("Mumega-com/inkwell") == "kasra"


def test_owner_for_unknown_repo_falls_back_to_default():
    # Default is `kasra` per repo_owners.json; change both together.
    assert owner_for("Mumega-com/does-not-exist") == "kasra"


def test_owner_entry_known_returns_agent_and_role():
    entry = owner_entry("Mumega-com/mumega-edge")
    assert entry is not None
    assert entry["agent"] == "kasra"
    assert "edge" in entry["role"].lower()


def test_owner_entry_unknown_returns_none():
    assert owner_entry("Mumega-com/does-not-exist") is None


def test_all_repos_includes_core_mothership_surfaces():
    repos = dict(all_repos())
    # The three mothership-relevant surfaces must always resolve.
    for surface in (
        "Mumega-com/inkwell",
        "Mumega-com/mumega-edge",
        "Mumega-com/sos",
    ):
        assert surface in repos, f"{surface} must be in repo_owners.json"


def test_every_entry_has_agent_and_role():
    for repo, entry in all_repos():
        assert entry["agent"], f"{repo} has no agent"
        assert entry["role"], f"{repo} has no role"


def test_forks_all_owned_by_same_agent():
    # Per-instance forks should route to one place until the ownership
    # map fans out — a regression here would silently split the fleet.
    forks = [
        owner_for("Mumega-com/digid-inkwell"),
        owner_for("Mumega-com/shabrang-inkwell"),
        owner_for("Mumega-com/mumega-internal-inkwell"),
    ]
    assert len(set(forks)) == 1, f"forks split across agents: {forks}"
