"""Tests for Phase 7 Step 7.3 growth-intel squad."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sos.agents.growth_intel import DossierWriter, NarrativeSynth, TrendFinder
from sos.contracts.brand_vector import BrandVector, Dossier
from sos.contracts.ports.integrations import ProviderParams, ProviderSnapshot
from sos.services.integrations.providers import FakeIntelligenceProvider


class _BoomProvider:
    kind = "fake"

    async def pull(self, tenant, params):
        raise RuntimeError("upstream 500")


# ---------------------------------------------------------------------------
# TrendFinder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trend_finder_fans_out_across_providers() -> None:
    tf = TrendFinder(providers=[FakeIntelligenceProvider(), FakeIntelligenceProvider()])
    snaps, errors = await tf.run("acme", ProviderParams(source_id="x"))
    assert len(snaps) == 2
    assert errors == []


@pytest.mark.asyncio
async def test_trend_finder_captures_errors_without_raising() -> None:
    tf = TrendFinder(providers=[FakeIntelligenceProvider(), _BoomProvider()])
    snaps, errors = await tf.run("acme", ProviderParams(source_id="x"))
    assert len(snaps) == 1
    assert len(errors) == 1
    assert errors[0][0] == "fake"
    assert "upstream 500" in str(errors[0][1])


def test_trend_finder_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError):
        TrendFinder(providers=[])


# ---------------------------------------------------------------------------
# NarrativeSynth
# ---------------------------------------------------------------------------


def _snap(kind: str, payload: dict) -> ProviderSnapshot:
    return ProviderSnapshot(
        tenant="acme",
        kind=kind,  # type: ignore[arg-type]
        captured_at=datetime.now(timezone.utc),
        source_id="src",
        payload=payload,
    )


def test_narrative_synth_extracts_keywords_from_gsc_queries() -> None:
    synth = NarrativeSynth()
    snap = _snap(
        "gsc",
        {"top_queries": [
            {"query": "mumega organism automation"},
            {"query": "mumega organism brand vector"},
            {"query": "automation copilot ai"},
        ]},
    )
    vec = synth.synthesize("acme", [snap])

    assert vec.tenant == "acme"
    assert "mumega" in vec.opportunity_vector or "automation" in vec.opportunity_vector
    assert vec.confidence > 0.0
    assert "futurist" in vec.tone or "technical" in vec.tone


def test_narrative_synth_extracts_competitors_from_brightdata_rows() -> None:
    synth = NarrativeSynth()
    snap = _snap(
        "brightdata",
        {"rows": [
            {"competitor": "acme.ai", "keyword": "ai automation"},
            {"competitor": "acme.ai", "keyword": "brand vector"},
            {"competitor": "beta.io", "keyword": "ai automation"},
        ]},
    )
    vec = synth.synthesize("acme", [snap])
    assert "acme.ai" in vec.threat_vector
    assert "beta.io" in vec.threat_vector


def test_narrative_synth_empty_snapshots_yields_zero_confidence() -> None:
    vec = NarrativeSynth().synthesize("acme", [])
    assert isinstance(vec, BrandVector)
    assert vec.confidence == 0.0
    assert vec.opportunity_vector == []


# ---------------------------------------------------------------------------
# DossierWriter
# ---------------------------------------------------------------------------


def test_dossier_writer_renders_populated_vector() -> None:
    v = BrandVector(
        tenant="acme",
        computed_at=datetime.now(timezone.utc),
        tone=["futurist", "technical"],
        opportunity_vector=["ai automation", "brand vector", "mumega organism"],
        threat_vector=["acme.ai", "beta.io"],
        source_snapshot_ids=["gsc:abc123", "brightdata:def456"],
        confidence=0.7,
    )
    d = DossierWriter().render(v)
    assert isinstance(d, Dossier)
    assert d.tenant == "acme"
    assert "ai automation" in d.opportunities
    assert "acme.ai" in d.threats
    assert "# Brand Vector — acme" in d.markdown
    assert "Confidence 0.70" in d.summary


def test_dossier_writer_renders_empty_vector_with_helpful_prompt() -> None:
    v = BrandVector(tenant="acme", computed_at=datetime.now(timezone.utc))
    d = DossierWriter().render(v)
    assert "Connect GA" in d.summary
    assert d.opportunities == []
    assert d.threats == []


# ---------------------------------------------------------------------------
# Full chain end-to-end with fake providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_chain_fake_providers_end_to_end() -> None:
    tf = TrendFinder(providers=[FakeIntelligenceProvider()])
    snaps, errors = await tf.run("acme", ProviderParams(source_id="n/a"))
    assert errors == []

    vec = NarrativeSynth().synthesize("acme", snaps)
    assert vec.tenant == "acme"

    dossier = DossierWriter().render(vec)
    assert dossier.tenant == "acme"
    assert dossier.markdown.startswith("# Brand Vector — acme")
