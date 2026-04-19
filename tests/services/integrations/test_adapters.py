"""Tests for Phase 7 Step 7.2 BrightData + Apify adapters."""
from __future__ import annotations

import httpx
import pytest

from sos.contracts.ports.integrations import ProviderParams
from sos.services.integrations.adapters import (
    ApifyAdapter,
    BrightDataAdapter,
    FakeSnapshotAdapter,
    NotReady,
)


# ---------------------------------------------------------------------------
# BrightData
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brightdata_trigger_returns_run_id() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.brightdata.com"
        assert req.url.params.get("collector") == "coll_abc"
        assert req.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"collection_id": "run_xyz"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = BrightDataAdapter(api_token="tok", http_client=http)
    run_id = await a.trigger("acme", ProviderParams(source_id="coll_abc"))
    assert run_id == "run_xyz"


@pytest.mark.asyncio
async def test_brightdata_fetch_result_raises_not_ready_on_202() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = BrightDataAdapter(api_token="tok", http_client=http)
    with pytest.raises(NotReady):
        await a.fetch_result("acme", "run_xyz", ProviderParams(source_id="coll_abc"))


@pytest.mark.asyncio
async def test_brightdata_fetch_result_happy_path() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"keyword": "k1"}, {"keyword": "k2"}])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = BrightDataAdapter(api_token="tok", http_client=http)
    snap = await a.fetch_result("acme", "run_xyz", ProviderParams(source_id="coll_abc"))
    assert snap.kind == "brightdata"
    assert snap.payload["rows"] == [{"keyword": "k1"}, {"keyword": "k2"}]
    assert snap.payload["run_id"] == "run_xyz"


# ---------------------------------------------------------------------------
# Apify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apify_trigger_returns_run_id() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": "run_A"}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = ApifyAdapter(api_token="tok", http_client=http)
    run_id = await a.trigger("acme", ProviderParams(source_id="actor_X"))
    assert run_id == "run_A"


@pytest.mark.asyncio
async def test_apify_fetch_not_ready_when_running() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"status": "RUNNING"}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = ApifyAdapter(api_token="tok", http_client=http)
    with pytest.raises(NotReady):
        await a.fetch_result("acme", "run_A", ProviderParams(source_id="actor_X"))


@pytest.mark.asyncio
async def test_apify_fetch_succeeded_reads_dataset() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        calls.append(path)
        if path.startswith("/v2/actor-runs/"):
            return httpx.Response(
                200,
                json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds_1"}},
            )
        if path.startswith("/v2/datasets/ds_1/items"):
            return httpx.Response(200, json=[{"url": "a"}, {"url": "b"}])
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = ApifyAdapter(api_token="tok", http_client=http)
    snap = await a.fetch_result("acme", "run_A", ProviderParams(source_id="actor_X"))
    assert snap.kind == "apify"
    assert snap.payload["items"] == [{"url": "a"}, {"url": "b"}]
    assert snap.payload["dataset_id"] == "ds_1"
    assert any("/v2/actor-runs/run_A" in c for c in calls)
    assert any("/v2/datasets/ds_1/items" in c for c in calls)


# ---------------------------------------------------------------------------
# Fake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_snapshot_adapter_end_to_end() -> None:
    a = FakeSnapshotAdapter(kind="brightdata")
    rid = await a.trigger("acme", ProviderParams(source_id="coll_x"))
    snap = await a.fetch_result("acme", rid, ProviderParams(source_id="coll_x"))
    assert snap.kind == "brightdata"
    assert snap.metadata["fake"] is True
    assert len(snap.payload["rows"]) == 3
