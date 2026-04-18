"""Tests for P0-06 analytics→integrations decoupling.

Proves that analytics no longer reaches into integrations internals:

1. **Static**: no file under sos/services/analytics/ contains a
   ``sos.services.integrations`` import string.

2. **Behavioral**: the ingest pipeline calls
   ``AsyncIntegrationsClient.get_credentials`` with the expected
   (tenant, provider) pair and respects both ``None`` and dict returns.

Pattern mirrors ``tests/services/test_content_engine_decoupling.py``.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sos.services.analytics.ingest import AnalyticsIngester


ANALYTICS_DIR = Path(__file__).resolve().parents[2] / "sos" / "services" / "analytics"


# ---------------------------------------------------------------------------
# Static: analytics no longer imports integrations internals
# ---------------------------------------------------------------------------

def _collect_imports(source: str) -> list[str]:
    """Return fully-qualified module names referenced in ``import`` /
    ``from X import`` statements (top-level + nested)."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


def test_no_analytics_file_imports_integrations_service():
    """Sweep: no file under sos/services/analytics/ imports sos.services.integrations.*"""
    offenders: dict[str, list[str]] = {}
    for py in ANALYTICS_DIR.rglob("*.py"):
        source = py.read_text()
        bad = [
            name for name in _collect_imports(source)
            if name.startswith("sos.services.integrations")
        ]
        if bad:
            offenders[str(py.relative_to(ANALYTICS_DIR))] = bad
    assert offenders == {}, (
        f"analytics files still import integrations internals: {offenders}. "
        "P0-06 requires analytics reach integrations via clients/contracts only."
    )


def test_no_analytics_source_contains_integrations_oauth_string():
    """Source-string check: catches lazy/local imports the AST walk might miss
    if the file is dynamically constructed. Belt & suspenders for P0-06."""
    offenders: list[str] = []
    for py in ANALYTICS_DIR.rglob("*.py"):
        source = py.read_text()
        if "sos.services.integrations" in source:
            offenders.append(str(py.relative_to(ANALYTICS_DIR)))
    assert offenders == [], (
        f"analytics source still references sos.services.integrations: {offenders}"
    )


# ---------------------------------------------------------------------------
# Behavioral: AnalyticsIngester uses AsyncIntegrationsClient correctly
# ---------------------------------------------------------------------------


def _make_ingester(provider_id: str, source: str) -> AnalyticsIngester:
    kwargs: dict[str, str] = {
        "tenant_name": "viamar",
        "mirror_url": "http://localhost:8844",
        "mirror_token": "",
    }
    if source == "ga4":
        kwargs["ga4_property_id"] = provider_id
    elif source == "gsc":
        kwargs["gsc_domain"] = provider_id
    elif source == "clarity":
        kwargs["clarity_project_id"] = provider_id
    return AnalyticsIngester(**kwargs)


@pytest.mark.asyncio
async def test_ingest_ga4_calls_client_with_tenant_and_provider():
    """GA4 ingest must query the integrations client for (tenant, google_analytics)."""
    ingester = _make_ingester("properties/1", "ga4")
    ingester._integrations = AsyncMock()
    ingester._integrations.get_credentials = AsyncMock(return_value=None)

    result = await ingester.ingest_ga4(days=7)

    ingester._integrations.get_credentials.assert_awaited_once_with(
        "viamar", "google_analytics"
    )
    # None creds path: return empty string, do not make any HTTP call
    assert result == ""


@pytest.mark.asyncio
async def test_ingest_gsc_calls_client_with_tenant_and_provider():
    """GSC ingest must query the integrations client for (tenant, google_search_console)."""
    ingester = _make_ingester("sc-domain:viamar.com", "gsc")
    ingester._integrations = AsyncMock()
    ingester._integrations.get_credentials = AsyncMock(return_value=None)

    result = await ingester.ingest_gsc(days=7)

    ingester._integrations.get_credentials.assert_awaited_once_with(
        "viamar", "google_search_console"
    )
    assert result == ""


@pytest.mark.asyncio
async def test_ingest_clarity_calls_client_with_tenant_and_provider():
    """Clarity ingest must query the integrations client for (tenant, clarity)."""
    ingester = _make_ingester("clarity-proj", "clarity")
    ingester._integrations = AsyncMock()
    ingester._integrations.get_credentials = AsyncMock(return_value=None)

    result = await ingester.ingest_clarity(days=7)

    ingester._integrations.get_credentials.assert_awaited_once_with(
        "viamar", "clarity"
    )
    assert result == ""


@pytest.mark.asyncio
async def test_ingest_ga4_with_dict_creds_proceeds_to_http_call():
    """When credentials come back as a dict, the ingest must use the
    access_token and attempt the GA4 HTTP call. We intercept the GA4
    call via httpx mocking to avoid reaching the real internet."""
    ingester = _make_ingester("properties/1", "ga4")
    ingester._integrations = AsyncMock()
    ingester._integrations.get_credentials = AsyncMock(
        return_value={"access_token": "ya29.stub"}
    )

    # Patch the sync httpx client's .post to record the call and return no rows
    class _FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:  # noqa: D401
            return None

        def json(self) -> dict[str, list]:
            return {"rows": []}

    called: dict[str, object] = {}

    def _fake_post(url: str, *, json: dict, headers: dict) -> _FakeResp:
        called["url"] = url
        called["auth"] = headers.get("Authorization")
        return _FakeResp()

    with patch.object(ingester._client, "post", side_effect=_fake_post):
        result = await ingester.ingest_ga4(days=7)

    ingester._integrations.get_credentials.assert_awaited_once_with(
        "viamar", "google_analytics"
    )
    assert called.get("auth") == "Bearer ya29.stub"
    # No rows → empty string report
    assert result == ""


@pytest.mark.asyncio
async def test_integrations_client_import_shape():
    """AnalyticsIngester must import AsyncIntegrationsClient, not TenantIntegrations."""
    from sos.services.analytics import ingest as ingest_module

    assert hasattr(ingest_module, "AsyncIntegrationsClient")
    # The module must NOT have surfaced any integrations internals:
    assert not hasattr(ingest_module, "TenantIntegrations"), (
        "Analytics ingest module must not import TenantIntegrations"
    )
