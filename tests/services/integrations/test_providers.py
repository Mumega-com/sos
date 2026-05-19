"""Tests for Phase 7 Step 7.1 intelligence providers."""
from __future__ import annotations

import httpx
import pytest

from sos.contracts.ports.integrations import (
    IntelligenceProvider,
    ProviderParams,
    ProviderSnapshot,
)
from sos.services.integrations.providers import (
    FakeIntelligenceProvider,
    GA4Provider,
    GSCProvider,
    GoogleAdsProvider,
)


async def _canned_token(tenant: str, provider: str) -> str:
    return f"access-{tenant}-{provider}"


# ---------------------------------------------------------------------------
# Fake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_provider_returns_canned_snapshot() -> None:
    prov = FakeIntelligenceProvider()
    snap = await prov.pull("acme", ProviderParams(source_id="n/a", range_days=30))

    assert isinstance(snap, ProviderSnapshot)
    assert snap.kind == "fake"
    assert snap.tenant == "acme"
    assert snap.payload["top_pages"][0]["pagePath"] == "/"
    assert snap.metadata["fake"] is True


# ---------------------------------------------------------------------------
# GA4
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ga4_provider_posts_runreport_with_bearer() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        captured["body"] = req.content
        return httpx.Response(
            200,
            json={"rows": [{"dimensionValues": [{"value": "/"}], "metricValues": [{"value": "1200"}]}]},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prov = GA4Provider(access_token_lookup=_canned_token, http_client=http)

    snap = await prov.pull("acme", ProviderParams(source_id="123456", range_days=30))

    assert snap.kind == "ga4"
    assert snap.source_id == "123456"
    assert "properties/123456:runReport" in captured["url"]
    assert captured["auth"] == "Bearer access-acme-google_analytics"
    assert snap.payload["rows"][0]["metricValues"][0]["value"] == "1200"


@pytest.mark.asyncio
async def test_ga4_provider_raises_on_4xx() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prov = GA4Provider(access_token_lookup=_canned_token, http_client=http)

    with pytest.raises(httpx.HTTPStatusError):
        await prov.pull("acme", ProviderParams(source_id="1", range_days=7))


# ---------------------------------------------------------------------------
# GSC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gsc_provider_url_encodes_site() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(
            200,
            json={"rows": [{"keys": ["mumega organism"], "impressions": 420, "clicks": 38}]},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prov = GSCProvider(access_token_lookup=_canned_token, http_client=http)

    snap = await prov.pull(
        "acme",
        ProviderParams(source_id="https://mumega.com/", range_days=14),
    )

    assert snap.kind == "gsc"
    # https:// gets percent-encoded — the site token is in the path.
    assert "https%3A%2F%2Fmumega.com%2F" in captured["url"]
    assert snap.payload["rows"][0]["keys"] == ["mumega organism"]


# ---------------------------------------------------------------------------
# Google Ads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ads_provider_sends_developer_token_header() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        captured["dev_token"] = req.headers.get("developer-token")
        return httpx.Response(200, json={"results": [{"campaign": {"name": "Playbook"}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prov = GoogleAdsProvider(
        access_token_lookup=_canned_token,
        developer_token="dev-token-123",
        http_client=http,
    )

    snap = await prov.pull("acme", ProviderParams(source_id="9876543210", range_days=30))

    assert snap.kind == "google_ads"
    assert captured["auth"] == "Bearer access-acme-google_ads"
    assert captured["dev_token"] == "dev-token-123"
    assert snap.payload["results"][0]["campaign"]["name"] == "Playbook"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_all_providers_satisfy_protocol() -> None:
    # runtime_checkable Protocol — instances must pass isinstance.
    ga4 = GA4Provider(access_token_lookup=_canned_token)
    gsc = GSCProvider(access_token_lookup=_canned_token)
    ads = GoogleAdsProvider(access_token_lookup=_canned_token)
    fake = FakeIntelligenceProvider()

    for p in (ga4, gsc, ads, fake):
        assert isinstance(p, IntelligenceProvider)
