"""Growth-intel squad — trend-finder + narrative-synth + dossier-writer.

Pulls external signals (GA4 / GSC / Ads / BrightData / Apify) via the
integrations providers + adapters, clusters them into a BrandVector,
renders a markdown Dossier. Bus wiring lands in Step 7.4; the classes
here are pure and directly testable.
"""

from __future__ import annotations

from sos.agents.growth_intel.dossier_writer import DossierWriter
from sos.agents.growth_intel.narrative_synth import NarrativeSynth
from sos.agents.growth_intel.trend_finder import TrendFinder

__all__ = ["DossierWriter", "NarrativeSynth", "TrendFinder"]
