"""v0.5.0 — governance budget integration tests.

Verifies that `sos.kernel.governance.before_action` correctly consults
AsyncEconomyClient.can_spend over HTTP and handles three paths:

1. Budget allowed    → allowed=True, tier=act_freely
2. Budget blocked    → allowed=False, tier=budget_exceeded
3. HTTP error        → fail-open (allowed=True, act_freely tier preserved)

No external services required — can_spend is patched in all tests.
asyncio_mode="auto" is set in pyproject.toml; do NOT add @pytest.mark.asyncio.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch


async def test_before_action_allows_when_budget_headroom_exists(tmp_path, monkeypatch):
    """Happy path: economy reports headroom → governance allows the action."""
    import sos.kernel.governance as gov
    import sos.kernel.audit as audit

    monkeypatch.setattr(gov, "_governance_dir", lambda: tmp_path / "governance")
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    with patch.object(gov._economy_client, "can_spend", new_callable=AsyncMock) as mock_can_spend:
        mock_can_spend.return_value = {
            "allowed": True,
            "budget": 100.0,
            "spent": 10.0,
            "remaining": 90.0,
            "pct_used": 10.0,
            "reason": "",
        }
        result = await gov.before_action(
            agent="testagent",
            action="content_publish",
            target="post_1",
            tenant="testtenant",
            metadata={"estimated_cost": 0.01, "project": "testproj"},
        )

    assert result["allowed"] is True
    assert result["tier"] == "act_freely"
    assert "intent_id" in result
    mock_can_spend.assert_called_once_with("testproj", 0.01)


async def test_before_action_blocks_when_budget_exceeded(tmp_path, monkeypatch):
    """Economy reports over-budget → governance blocks and returns budget_exceeded tier."""
    import sos.kernel.governance as gov
    import sos.kernel.audit as audit

    monkeypatch.setattr(gov, "_governance_dir", lambda: tmp_path / "governance")
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    with patch.object(gov._economy_client, "can_spend", new_callable=AsyncMock) as mock_can_spend:
        mock_can_spend.return_value = {
            "allowed": False,
            "budget": 100.0,
            "spent": 105.0,
            "remaining": -5.0,
            "pct_used": 105.0,
            "reason": "budget exceeded by $5",
        }
        result = await gov.before_action(
            agent="testagent",
            action="content_publish",
            target="post_1",
            tenant="testtenant",
            metadata={"estimated_cost": 0.01, "project": "testproj"},
        )

    assert result["allowed"] is False
    assert result["tier"] == "budget_exceeded"
    assert "budget exceeded" in result["reason"]
    assert result["budget"]["spent"] == 105.0
    mock_can_spend.assert_called_once_with("testproj", 0.01)


async def test_before_action_fails_open_on_http_error(tmp_path, monkeypatch):
    """Economy unreachable → governance fails-open (allowed=True).

    The v0.5.0 availability guarantee: HTTP errors must NEVER block
    governance. An agent must be able to act even when economy is down.
    """
    import sos.kernel.governance as gov
    import sos.kernel.audit as audit

    monkeypatch.setattr(gov, "_governance_dir", lambda: tmp_path / "governance")
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    with patch.object(gov._economy_client, "can_spend", new_callable=AsyncMock) as mock_can_spend:
        mock_can_spend.side_effect = ConnectionError("economy unreachable")
        result = await gov.before_action(
            agent="testagent",
            action="content_publish",
            target="post_1",
            tenant="testtenant",
            metadata={"estimated_cost": 0.01, "project": "testproj"},
        )

    # Fail-open: HTTP errors must not block governance
    assert result["allowed"] is True
    # content_publish maps to act_freely in DEFAULT_POLICY
    assert result["tier"] == "act_freely"
    assert "intent_id" in result
