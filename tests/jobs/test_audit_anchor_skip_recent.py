"""
Sprint 007 G55-WARN2 — verifier skip-recent-anchor tests (3 TCs).

TC-G55-WARN2-a: Recent anchor (30s old) → ok=True, reason=skipped_recent_anchor
TC-G55-WARN2-b: Old anchor (120s old) → proceeds to R2 fetch (not skipped)
TC-G55-WARN2-c: Skipped anchor doesn't trigger emit_verifier_drift_detected
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# TC-G55-WARN2-a: recent anchor → skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g55_warn2_a_recent_anchor_skipped() -> None:
    """TC-G55-WARN2-a: anchor 30s old → ok=True, reason=skipped_recent_anchor."""
    from sos.jobs.audit_anchor import _verify_stream

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={
        "anchored_seq": 42,
        "anchor_hash": "abc123",
        "prev_anchor_hash": "def456",
        "r2_object_key": "anchors/2026/04/26/test-42.json",
        "anchored_at": datetime.now(timezone.utc) - timedelta(seconds=30),
    })

    mock_s3 = MagicMock()

    result = await _verify_stream(mock_conn, "test_stream", mock_s3, "test-bucket")

    assert result["ok"] is True
    assert result["reason"] == "skipped_recent_anchor"
    assert result.get("skipped") is True
    # R2 fetch should NOT have been called
    mock_s3.get_object.assert_not_called()


# ---------------------------------------------------------------------------
# TC-G55-WARN2-b: old anchor → proceeds to R2 fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g55_warn2_b_old_anchor_proceeds() -> None:
    """TC-G55-WARN2-b: anchor 120s old → proceeds to R2 fetch (not skipped)."""
    from sos.jobs.audit_anchor import _verify_stream

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={
        "anchored_seq": 42,
        "anchor_hash": "abc123def456",
        "prev_anchor_hash": None,
        "r2_object_key": "anchors/2026/04/26/test-42.json",
        "anchored_at": datetime.now(timezone.utc) - timedelta(seconds=120),
    })

    # R2 fetch will fail (we're just checking it tries)
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = RuntimeError("R2 unreachable")

    result = await _verify_stream(mock_conn, "test_stream", mock_s3, "test-bucket")

    # Should have attempted R2 fetch (not skipped)
    assert result.get("skipped") is not True
    assert "r2_fetch_failed" in result.get("reason", "")


# ---------------------------------------------------------------------------
# TC-G55-WARN2-c: skipped doesn't trigger drift emit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g55_warn2_c_skipped_no_drift_emit() -> None:
    """TC-G55-WARN2-c: skipped anchor → no emit_verifier_drift_detected call."""
    from sos.jobs.audit_anchor import _verify_stream

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={
        "anchored_seq": 42,
        "anchor_hash": "abc123",
        "prev_anchor_hash": "def456",
        "r2_object_key": "anchors/2026/04/26/test-42.json",
        "anchored_at": datetime.now(timezone.utc) - timedelta(seconds=10),
    })

    mock_s3 = MagicMock()

    with patch("sos.observability.sprint_telemetry.emit_verifier_drift_detected") as mock_emit:
        result = await _verify_stream(mock_conn, "test_stream", mock_s3, "test-bucket")

    assert result["ok"] is True
    assert result["reason"] == "skipped_recent_anchor"
    mock_emit.assert_not_called()
