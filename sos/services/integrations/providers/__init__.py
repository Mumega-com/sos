"""Intelligence providers for the growth-intel squad (Phase 7).

Each provider implements `sos.contracts.ports.integrations.IntelligenceProvider`.
Live providers hit real APIs via httpx; the `FakeIntelligenceProvider` returns
canned data so the squad loop runs end-to-end in dev + CI without creds.
"""

from __future__ import annotations

from sos.services.integrations.providers.ads import GoogleAdsProvider
from sos.services.integrations.providers.fake import FakeIntelligenceProvider
from sos.services.integrations.providers.ga4 import GA4Provider
from sos.services.integrations.providers.gsc import GSCProvider

__all__ = [
    "FakeIntelligenceProvider",
    "GA4Provider",
    "GSCProvider",
    "GoogleAdsProvider",
]
