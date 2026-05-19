"""Tests for sos.services.operations.pulse.

The pulse posts a root objective plus one child per standing workflow via
:class:`sos.clients.objectives.AsyncObjectivesClient`. We monkeypatch
``AsyncObjectivesClient.create`` to capture calls without hitting the wire.
"""
from __future__ import annotations

from typing import Any

import pytest

from sos.clients.objectives import AsyncObjectivesClient
from sos.contracts.objective import Objective
from sos.services.operations import pulse

# Valid-ULID IDs use Crockford base-32 (no I, L, O, U).
ULID_ROOT = "01HWZZZZZZZZZZZZZZZZRT0001"  # 26 chars, Crockford-safe
ULID_CHILD_PREFIX = "01HWZZZZZZZZZZZZZZZZCH"  # 22 chars; suffix brings to 26


# Workflow catalog fixture — simulates what a tenant repo would ship as
# its ``standing_workflows.json``. Tests pass this via the ``workflows=``
# kwarg, keeping them free of any tenant-specific file I/O.
FAKE_WORKFLOWS: list[dict[str, Any]] = [
    {
        "name": "harvest-winners",
        "bounty_mind": 100,
        "tags": ["kind:harvest-winners", "daily-rhythm"],
        "capabilities_required": [],
        "description": "harvest top-decile winners into the demo bank",
    },
    {
        "name": "daily-social-post",
        "bounty_mind": 500,
        "tags": ["social", "daily-rhythm"],
        "capabilities_required": ["post-instagram"],
        "description": "draft + publish today's social post",
    },
    {
        "name": "daily-blog-draft",
        "bounty_mind": 2000,
        "tags": ["content", "daily-rhythm"],
        "capabilities_required": ["blog-draft"],
        "description": "draft a long-form blog post",
    },
]


def _mk_objective(obj_id: str, **overrides: Any) -> Objective:
    now = Objective.now_iso()
    defaults: dict[str, Any] = {
        "id": obj_id,
        "title": "x",
        "created_by": "pulse:test",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Objective(**defaults)


@pytest.mark.asyncio
async def test_post_daily_rhythm_creates_root_and_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """post_daily_rhythm emits one root + N child creates with expected fields."""
    calls: list[dict[str, Any]] = []
    counter = {"n": 0}

    async def fake_create(self: AsyncObjectivesClient, title: str, **kwargs: Any) -> Objective:
        calls.append({"title": title, **kwargs})
        if "parent_id" in kwargs and kwargs["parent_id"] is not None:
            counter["n"] += 1
            return _mk_objective(f"{ULID_CHILD_PREFIX}{counter['n']:04d}")
        return _mk_objective(ULID_ROOT)

    monkeypatch.setattr(AsyncObjectivesClient, "create", fake_create)

    workflows = FAKE_WORKFLOWS
    assert workflows[0]["name"] == "harvest-winners"

    root_id = await pulse.post_daily_rhythm("trop", workflows=workflows)

    assert root_id == ULID_ROOT
    # 1 root + 3 children (len(FAKE_WORKFLOWS))
    assert len(calls) == 1 + len(workflows)

    # Root call: no parent_id, project=trop, tags include daily-rhythm
    root_call = calls[0]
    assert root_call.get("parent_id") is None
    assert root_call["project"] == "trop"
    assert root_call["tenant_id"] == "trop"
    assert "daily-rhythm" in root_call["tags"]
    assert root_call["created_by"] == "pulse:trop"

    # Children: one per workflow, each references the root, carries bounty +
    # capabilities from the workflow definition.
    child_calls = calls[1:]
    assert {c["title"] for c in child_calls} == {wf["name"] for wf in workflows}

    expected_bounties = {wf["name"]: wf["bounty_mind"] for wf in workflows}
    for c in child_calls:
        assert c["parent_id"] == ULID_ROOT
        assert c["project"] == "trop"
        assert c["bounty_mind"] == expected_bounties[c["title"]]
        # Capabilities flow through unchanged — this is the hook S4 needs.
        wf = next(w for w in workflows if w["name"] == c["title"])
        assert c["capabilities_required"] == wf["capabilities_required"]


@pytest.mark.asyncio
async def test_post_daily_rhythm_fail_soft_on_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the root create raises, post_daily_rhythm returns "" without raising."""

    async def boom(self: AsyncObjectivesClient, title: str, **kwargs: Any) -> Objective:
        raise RuntimeError("simulated objectives service outage")

    monkeypatch.setattr(AsyncObjectivesClient, "create", boom)

    # Must not raise — fail-soft contract.
    result = await pulse.post_daily_rhythm("trop", workflows=FAKE_WORKFLOWS)
    assert result == ""


# ---------------------------------------------------------------------------
# S6: noon pulse — single health-check child
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_noon_pulse_creates_health_check_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Noon pulse posts one root + one child tagged kind:health-check."""
    calls: list[dict[str, Any]] = []
    counter = {"n": 0}

    async def fake_create(self: AsyncObjectivesClient, title: str, **kwargs: Any) -> Objective:
        calls.append({"title": title, **kwargs})
        if kwargs.get("parent_id") is not None:
            counter["n"] += 1
            return _mk_objective(f"{ULID_CHILD_PREFIX}{counter['n']:04d}")
        return _mk_objective(ULID_ROOT)

    monkeypatch.setattr(AsyncObjectivesClient, "create", fake_create)

    root_id = await pulse.post_noon_pulse("trop")

    assert root_id == ULID_ROOT
    # 1 root + 1 health-check child — noon is intentionally small.
    assert len(calls) == 2

    root_call, child_call = calls
    assert root_call.get("parent_id") is None
    assert root_call["project"] == "trop"
    assert "noon-pulse" in root_call["tags"]

    assert child_call["parent_id"] == ULID_ROOT
    assert child_call["title"] == "check-health-of-objectives"
    assert "kind:health-check" in child_call["tags"]
    assert child_call["bounty_mind"] == 50
    assert child_call["capabilities_required"] == ["health-check"]


@pytest.mark.asyncio
async def test_post_noon_pulse_fail_soft_on_root_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the noon root create raises, the pulse returns "" without raising."""

    async def boom(self: AsyncObjectivesClient, title: str, **kwargs: Any) -> Objective:
        raise RuntimeError("noon outage")

    monkeypatch.setattr(AsyncObjectivesClient, "create", boom)

    result = await pulse.post_noon_pulse("trop")
    assert result == ""


# ---------------------------------------------------------------------------
# S6: evening pulse — postmortem + harvest-winners children
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_evening_pulse_creates_postmortem_and_harvest_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evening pulse posts one root + a postmortem child + a harvest child."""
    calls: list[dict[str, Any]] = []
    counter = {"n": 0}

    async def fake_create(self: AsyncObjectivesClient, title: str, **kwargs: Any) -> Objective:
        calls.append({"title": title, **kwargs})
        if kwargs.get("parent_id") is not None:
            counter["n"] += 1
            return _mk_objective(f"{ULID_CHILD_PREFIX}{counter['n']:04d}")
        return _mk_objective(ULID_ROOT)

    monkeypatch.setattr(AsyncObjectivesClient, "create", fake_create)

    root_id = await pulse.post_evening_pulse("trop")

    assert root_id == ULID_ROOT
    # 1 root + 2 children (postmortem + harvest-winners).
    assert len(calls) == 3

    root_call = calls[0]
    assert root_call.get("parent_id") is None
    assert "evening-pulse" in root_call["tags"]

    postmortem = next(c for c in calls if c["title"] == "evening-postmortem")
    assert postmortem["parent_id"] == ULID_ROOT
    assert "kind:postmortem" in postmortem["tags"]
    assert postmortem["bounty_mind"] == 200

    harvest = next(c for c in calls if c["title"] == "harvest-winners")
    assert harvest["parent_id"] == ULID_ROOT
    assert "kind:harvest-winners" in harvest["tags"]
    assert harvest["bounty_mind"] == 100


@pytest.mark.asyncio
async def test_post_morning_pulse_alias_matches_post_daily_rhythm() -> None:
    """post_morning_pulse is the BC alias — must be identical object."""
    assert pulse.post_morning_pulse is pulse.post_daily_rhythm


# ---------------------------------------------------------------------------
# load_standing_workflows — tenant-agnostic file reader (v0.8.2)
# ---------------------------------------------------------------------------


def test_load_standing_workflows_returns_empty_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """No file + no env → empty list, no raise."""
    monkeypatch.delenv("SOS_PULSE_WORKFLOWS_FILE", raising=False)
    monkeypatch.delenv("SOS_PULSE_WORKFLOWS_FILE_TROP", raising=False)
    assert pulse.load_standing_workflows("trop") == []


def test_load_standing_workflows_reads_explicit_file(tmp_path: Any) -> None:
    path = tmp_path / "wf.json"
    path.write_text('[{"name":"x","bounty_mind":10,"tags":[],"capabilities_required":[],"description":""}]')
    result = pulse.load_standing_workflows("trop", workflows_file=str(path))
    assert len(result) == 1
    assert result[0]["name"] == "x"


def test_load_standing_workflows_per_project_env_wins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Env SOS_PULSE_WORKFLOWS_FILE_<PROJECT> beats the shared var."""
    shared = tmp_path / "shared.json"
    shared.write_text('[{"name":"shared"}]')
    per = tmp_path / "per.json"
    per.write_text('[{"name":"per-project"}]')
    monkeypatch.setenv("SOS_PULSE_WORKFLOWS_FILE", str(shared))
    monkeypatch.setenv("SOS_PULSE_WORKFLOWS_FILE_TROP", str(per))

    result = pulse.load_standing_workflows("trop")
    assert result == [{"name": "per-project"}]


def test_load_standing_workflows_missing_file_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Unreadable file path → fail-soft empty list (never raise)."""
    ghost = tmp_path / "does-not-exist.json"
    result = pulse.load_standing_workflows("trop", workflows_file=str(ghost))
    assert result == []


def test_load_standing_workflows_bad_shape_returns_empty(tmp_path: Any) -> None:
    """Non-array JSON is treated as empty, never raises."""
    path = tmp_path / "wrong.json"
    path.write_text('{"not":"an array"}')
    assert pulse.load_standing_workflows("trop", workflows_file=str(path)) == []
